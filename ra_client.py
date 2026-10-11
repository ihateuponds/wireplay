"""RetroAchievements: per-game achievement progress for the retro consoles, plus cached badge icons.

Enabled only when ~/.config/wireplay/config.json has both "ra_username" and "ra_api_key" (a free web API key from
retroachievements.org -> Control Panel). Without them every function here returns an empty/None result and nothing
touches the network, so the feature is simply absent.

Endpoints used (documented at api-docs.retroachievements.org; auth is the `y=<key>` query parameter):
    API_GetConsoleIDs.php                 system id <-> name (used to double-check the id table below)
    API_GetGameList.php?i=<id>&f=1        games of one system that have achievements (cached 7 days)
    API_GetUserCompletionProgress.php     the user's progress over ALL games, 500 per page  -> card badges
    API_GetGameInfoAndUserProgress.php    one game: every achievement with the user's unlock state -> drawer
Badges come from media.retroachievements.org/Badge/<name>[_lock].png and are cached in ~/.cache/wireplay/ra_badges/.

The API key is only ever put in a request URL; error text is scrubbed of it before it leaves this module.
Everything that talks to the network is for worker threads (QRunnable), never the UI thread.
"""
import hashlib
import json
import os
import re
import time
import urllib.parse
import zipfile
from pathlib import Path
from typing import Optional

import config
import covers
import db
import metadata
from covers import NetworkError

API_BASE = "https://retroachievements.org/API/"
MEDIA_BASE = "https://media.retroachievements.org/"
CACHE_DIR = Path(os.environ.get("WIREPLAY_CACHE") or Path.home() / ".cache/wireplay")
BADGE_DIR = CACHE_DIR / "ra_badges"
DATA_DIR = CACHE_DIR / "ra"
GAME_LIST_TTL = 7 * 86400
CONSOLE_LIST_TTL = 30 * 86400
SYNC_EVERY = 6 * 3600            # library sync at most this often (also when the app is restarted)
MAX_JSON_BYTES = 12 << 20        # a game list for a big system is a few MB
MAX_BADGE_BYTES = 256 << 10
REQUEST_GAP = 0.25               # seconds between consecutive API calls of one sync: stay well inside "reasonable use"

# wireplay platform -> (RetroAchievements system id, regex the system's NAME must match).
# The ids are the documented ones (1 = Mega Drive is in the docs' own example); the name check guards against a
# wrong entry: when the live console list is available an id whose name does not match is looked up by name.
CONSOLES = {
    "genesis": (1, r"mega drive|genesis"), "megadrive": (1, r"mega drive|genesis"),
    "n64": (2, r"nintendo 64"), "snes": (3, r"snes|super"), "gb": (4, r"^game boy$"),
    "gba": (5, r"game boy advance"), "gbc": (6, r"game boy color"), "nes": (7, r"nes|famicom"),
    "psx": (12, r"^playstation$"), "gc": (16, r"gamecube"), "wii": (19, r"^wii$"),
    "nds": (18, r"nintendo ds$"), "ps2": (21, r"playstation 2"), "psp": (41, r"playstation portable|psp"),
}
_UNTAGGED = re.compile(r"^\s*(?:~[^~]*~\s*)+")        # RetroAchievements prefixes: ~Hack~, ~Homebrew~, ~Demo~ ...
_SUFFIX_WORDS = ("version", "edition")
_BADGE_NAME = re.compile(r"^[A-Za-z0-9_]{1,40}$")

_failed_credentials = None       # (user, key) the server rejected: not retried until the config changes


# ---------------------------------------------------------------- credentials + HTTP

def credentials():
    """(username, api key) from the config, or None when RetroAchievements is not set up."""
    cfg = config.load()
    user, key = cfg.get("ra_username"), cfg.get("ra_api_key")
    if isinstance(user, str) and isinstance(key, str) and user.strip() and key.strip():
        creds = (user.strip(), key.strip())
        return None if creds == _failed_credentials else creds
    return None


def enabled() -> bool:
    return credentials() is not None


def supports(platform: str) -> bool:
    return platform in CONSOLES


def _scrub(text: str, key: str) -> str:
    return text.replace(key, "***") if key else text


def _api(endpoint: str, **params):
    """Parsed JSON of one API call. Raises NetworkError (credentials rejected, rate limit, outage, bad answer)."""
    global _failed_credentials
    creds = credentials()
    if creds is None:
        raise NetworkError("RetroAchievements is not configured")
    user, key = creds
    query = "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in {"y": key, "u": user, **params}.items())
    try:
        body = covers.http_get(f"{API_BASE}{endpoint}?{query}", timeout=25, max_bytes=MAX_JSON_BYTES, missing=())
        data = json.loads(body or b"null")
    except NetworkError as e:
        if e.status in (401, 403):
            _failed_credentials = creds
        raise NetworkError(_scrub(str(e), key), e.status) from None
    except ValueError:
        raise NetworkError("RetroAchievements returned something that is not JSON") from None
    if data is None:
        raise NetworkError("RetroAchievements gave an empty answer")
    if isinstance(data, dict) and data.get("Error"):
        if "key" in str(data.get("Error")).lower() or "unauthor" in str(data.get("Error")).lower():
            _failed_credentials = creds
        raise NetworkError(_scrub(f"RetroAchievements: {data['Error']}", key))
    return data


# ---------------------------------------------------------------- small JSON cache

def _cached(name: str, ttl: float, loader):
    """loader() result cached in DATA_DIR/<name>.json for `ttl` seconds. A stale copy is kept if loader() fails."""
    path = DATA_DIR / f"{name}.json"
    stale = None
    try:
        if path.is_file():
            stale = json.loads(path.read_text())
            if time.time() - path.stat().st_mtime < ttl:
                return stale
    except (OSError, ValueError):
        stale = None
    try:
        fresh = loader()
    except NetworkError:
        if stale is not None:
            return stale
        raise
    covers.store(path, json.dumps(fresh).encode())
    return fresh


def console_ids() -> dict:
    """{platform: system id}: the CONSOLES table, corrected by name against RetroAchievements' own list when it
    can be fetched. A platform with no matching system is left out (no badges for it) rather than guessed."""
    try:
        listing = _cached("consoles", CONSOLE_LIST_TTL, lambda: _api("API_GetConsoleIDs.php"))
    except NetworkError:
        listing = None
    names = {int(c["ID"]): str(c.get("Name", "")) for c in listing or [] if isinstance(c, dict) and "ID" in c}
    out = {}
    for platform, (cid, pattern) in CONSOLES.items():
        if not names:
            out[platform] = cid                           # list unavailable: trust the table
        elif re.search(pattern, names.get(cid, "").lower()):
            out[platform] = cid
        else:
            hit = next((i for i, n in names.items() if re.search(pattern, n.lower())), None)
            if hit is not None:
                out[platform] = hit
    return out


# ---------------------------------------------------------------- matching local ROMs to RetroAchievements sets

def match_key(title: str) -> str:
    """Normalized title with RetroAchievements' ~Hack~ style prefixes and trailing 'Version'/'Edition' removed."""
    key = metadata.normalize_title(_UNTAGGED.sub("", title or ""))
    words = key.split()
    while len(words) > 1 and words[-1] in _SUFFIX_WORDS:
        words.pop()
    return " ".join(words)


def game_list(console_id: int) -> list:
    """[{"id", "title", "n", "h": [md5 ...]}] of the system's games that have achievements (cached a week).

    The hashes (API_GetGameList h=1) are the ROMs RetroAchievements supports for each set; they let a ROM be matched
    exactly (see identify_row) without a request per ROM.
    """
    def load():
        raw = _api("API_GetGameList.php", i=console_id, f=1, h=1)
        time.sleep(REQUEST_GAP)
        return [{"id": int(g["ID"]), "title": str(g.get("Title", "")), "n": int(g.get("NumAchievements") or 0),
                 "h": [str(h).lower() for h in (g.get("Hashes") or []) if isinstance(h, str)]}
                for g in raw if isinstance(g, dict) and g.get("ID") and int(g.get("NumAchievements") or 0) > 0]
    return _cached(f"gamesh_{int(console_id)}", GAME_LIST_TTL, load)


def hash_index(console_id: int) -> dict:
    """{md5: (ra game id, achievement count)} for one system."""
    return {h: (g["id"], g["n"]) for g in game_list(console_id) for h in g.get("h", [])}


_TRAILING_TAG = re.compile(r"\[([^\]]*)\]\s*$")
_PLAIN_TAG = re.compile(r"^(?:rev\.? ?[\w.]+|v ?\d[\w.]*|\d[\w.]*)$", re.I)      # Rev 1, v1.2.1, 1.0 ...


def _plain_revision(title: str) -> bool:
    """True when a title has no trailing [tag], or only revision tags ([Rev 1], [v1.2]). Translations, hacks and
    subsets ([De, v1.2.2], [Hack], [Subset - ...]) are different ROMs that do not earn the base game's achievements."""
    m = _TRAILING_TAG.search(title or "")
    return m is None or all(_PLAIN_TAG.match(part.strip()) for part in m.group(1).split(","))


def match_games(rows, games) -> list:
    """[(row id, ra game id, achievement count)] for rows whose title equals a set's title (see match_key).

    Order: the full title (so '[Subset - Low Level Run]' finds its own subset), then the title without its [tag],
    but only for plain revisions (never via the stack's base title, which other games share): an '[De, v1.2.2]' translation must not inherit the original game's
    trophies. A stack of many games (91 Pokémon versions) is matched row by row, never through its shared base title.
    Exact-key matching on purpose: fuzzy matching 5,800 ROMs against thousands of titles is slow and wrong often
    enough to put a trophy on the wrong game. Hash matching would be exact but needs the ROM files read.
    A real game wins over a ~Hack~ set with the same key.
    """
    index = {}
    for g in games:
        hack = bool(_UNTAGGED.match(g["title"]))
        key = match_key(g["title"])
        if key and (key not in index or (index[key][2] and not hack)):
            index[key] = (g["id"], g["n"], hack)
    out = []
    for r in rows:
        hit = index.get(match_key(r["title"]))
        if not hit and _plain_revision(r["title"]):
            hit = index.get(match_key(metadata.clean_title(r["title"])))
        elif not hit and re.search(r"hack", (_TRAILING_TAG.search(r["title"]) or [None, ""])[1], re.I):
            cand = index.get(match_key(metadata.clean_title(r["title"])))
            hit = cand if cand and cand[2] else None                     # a [Hack] ROM may use a ~Hack~ set, never the original's
        if hit:
            out.append((r["id"], hit[0], hit[1]))
    return out


def user_progress() -> dict:
    """{ra game id: (max possible, unlocked, hardcore unlocked, highest award kind)} for every game the user has progress in."""
    out, offset = {}, 0
    while True:
        page = _api("API_GetUserCompletionProgress.php", c=500, o=offset)
        time.sleep(REQUEST_GAP)
        results = page.get("Results") if isinstance(page, dict) else None
        if not results:
            break
        for g in results:
            out[int(g["GameID"])] = (int(g.get("MaxPossible") or 0), int(g.get("NumAwarded") or 0),
                                     int(g.get("NumAwardedHardcore") or 0), str(g.get("HighestAwardKind") or ""))
        offset += len(results)
        if offset >= int(page.get("Total") or 0):
            break
    return out


def sync_library() -> int:
    """Match local retro games to RetroAchievements sets and cache X/Y per game. Returns the rows with achievements.

    Worker-thread job: a handful of API calls (one game list per console, cached a week, plus the user's progress
    pages). Raises NetworkError when the service is unreachable; the caller treats that as 'try next time'.
    """
    if credentials() is None:
        return 0
    consoles = console_ids()
    with db.session() as con:
        present = {r["platform"] for r in con.execute("SELECT DISTINCT platform FROM games")}
    matched = {}
    for platform in sorted(present & set(consoles)):
        games = game_list(consoles[platform])
        with db.session() as con:
            rows = db.ra_candidates(con, [platform])
            found = {row_id: (ra_id, n) for row_id, ra_id, n in match_games(rows, games)}
            matched.update(found)
            current = {r["id"]: r["ra_game_id"] for r in rows}
            for row_id, (ra_id, n) in found.items():
                if current[row_id] != ra_id:
                    db.set_ra_match(con, row_id, ra_id, n)
            db.clear_ra_matches(con, [i for i, ra in current.items() if ra is not None and i not in found])   # no longer matches
    progress = user_progress()
    with db.session() as con:                          # every set any row is matched to, title- or hash-matched
        for ra_id, n in [(r[0], r[1]) for r in con.execute("SELECT DISTINCT ra_game_id, ra_total FROM games WHERE ra_game_id IS NOT NULL")]:
            total, unlocked, _hard, kind = progress.get(ra_id, (n, 0, 0, ""))
            total = total or n
            db.update_ra_progress(con, ra_id, total, unlocked, mastered=(total > 0 and unlocked >= total) or kind == "mastered")
        matched_rows = con.execute("SELECT COUNT(*) FROM games WHERE ra_game_id IS NOT NULL").fetchone()[0]
    _mark_synced()
    return matched_rows


def _mark_synced():
    covers.store(DATA_DIR / "last_sync", str(int(time.time())).encode())


def sync_due() -> bool:
    try:
        return time.time() - float((DATA_DIR / "last_sync").read_text()) > SYNC_EVERY
    except (OSError, ValueError):
        return True


# ---------------------------------------------------------------- one game (drawer)

def game_detail(ra_game_id: int) -> Optional[dict]:
    """The user's progress in one set: totals, hardcore points, mastery and every achievement with its unlock state."""
    raw = _api("API_GetGameInfoAndUserProgress.php", g=int(ra_game_id))
    if not isinstance(raw, dict) or not raw.get("ID"):
        return None
    ach = raw.get("Achievements")
    items = []
    for a in (ach.values() if isinstance(ach, dict) else []):
        if not isinstance(a, dict):
            continue
        items.append({
            "id": int(a.get("ID") or 0), "title": str(a.get("Title") or ""), "description": str(a.get("Description") or ""),
            "points": int(a.get("Points") or 0), "badge": str(a.get("BadgeName") or ""),
            "unlocked": bool(a.get("DateEarned") or a.get("DateEarnedHardcore")),
            "hardcore": bool(a.get("DateEarnedHardcore")), "date": str(a.get("DateEarnedHardcore") or a.get("DateEarned") or ""),
            "order": int(a.get("DisplayOrder") or 0),
        })
    items.sort(key=lambda a: (a["order"], a["id"]))
    total = int(raw.get("NumAchievements") or len(items))
    unlocked = int(raw.get("NumAwardedToUser") or sum(a["unlocked"] for a in items))
    return {
        "id": int(raw["ID"]), "title": str(raw.get("Title") or ""), "total": total, "unlocked": unlocked,
        "points": sum(a["points"] for a in items if a["hardcore"]),            # hardcore points earned
        "points_total": sum(a["points"] for a in items),
        "mastered": total > 0 and unlocked >= total, "achievements": items,
    }


def refresh_game(ra_game_id: int) -> Optional[dict]:
    """game_detail() plus storing the totals / points in the DB (worker thread). None when RA has no such set."""
    detail = game_detail(ra_game_id)
    if detail is None:
        return None
    with db.session() as con:
        db.update_ra_progress(con, detail["id"], detail["total"], detail["unlocked"], detail["points"], detail["mastered"])
    return detail


# ---------------------------------------------------------------- badges

def badge_path(name: str, locked: bool = False) -> Optional[Path]:
    """Where a badge is (or would be) cached; None for a name that is not a plain badge id."""
    if not _BADGE_NAME.match(name or ""):
        return None
    return BADGE_DIR / f"{name}{'_lock' if locked else ''}.png"


def cached_badge(name: str, locked: bool = False) -> Optional[Path]:
    path = badge_path(name, locked)
    return path if path and path.is_file() else None


def fetch_badge(name: str, locked: bool = False) -> Optional[Path]:
    """Cached badge file, downloading it first if needed. None when it does not exist. May raise NetworkError."""
    path = badge_path(name, locked)
    if path is None:
        return None
    if path.is_file():
        return path
    data = covers.http_get(f"{MEDIA_BASE}Badge/{path.name}", timeout=15, max_bytes=MAX_BADGE_BYTES)
    if not data or not data.startswith(b"\x89PNG"):
        return None
    return covers.store(path, data)


# ---------------------------------------------------------------- ROM hashing (on demand, never a bulk scan)

HASH_PLATFORMS = frozenset({"nes", "snes", "gb", "gbc", "gba", "n64", "genesis", "megadrive"})
MAX_HASH_BYTES = 80 << 20            # N64 / GBA cartridges top out far below this; anything bigger is not a cartridge ROM
_N64_MAGIC = {b"\x80\x37\x12\x40": "z64", b"\x37\x80\x40\x12": "v64", b"\x40\x12\x37\x80": "n64"}


def hash_bytes(platform: str, data: bytes):
    """The RetroAchievements hash (MD5 of the ROM as the emulators present it) of a cartridge image, or None.

    Header rules, as RetroAchievements applies them: NES drops the 16-byte iNES header; SNES drops a 512-byte copier
    header (file size % 1024 == 512); N64 images are normalised to big-endian (.z64) from .v64 / .n64; Game Boy,
    Game Boy Color, GBA and Mega Drive hash the whole file.
    """
    if platform not in HASH_PLATFORMS or not data:
        return None
    if platform == "nes" and data[:4] == b"NES\x1a":
        data = data[16:]
    elif platform == "snes" and len(data) % 1024 == 512:
        data = data[512:]
    elif platform == "n64":
        order = _N64_MAGIC.get(data[:4])
        if order == "v64":                               # byte-swapped: swap every pair
            b = bytearray(data)
            b[0::2], b[1::2] = data[1::2], data[0::2]
            data = bytes(b)
        elif order == "n64":                             # little-endian words: reverse every 4 bytes
            b = bytearray(data)
            b[0::4], b[1::4], b[2::4], b[3::4] = data[3::4], data[2::4], data[1::4], data[0::4]
            data = bytes(b)
    return hashlib.md5(data).hexdigest()


def read_rom(path) -> Optional[bytes]:
    """The ROM bytes of a file, or of the single file inside a .zip. None when unreadable, too big, or ambiguous."""
    path = Path(path)
    try:
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as z:
                entries = [i for i in z.infolist() if not i.is_dir()]
                if len(entries) != 1 or entries[0].file_size > MAX_HASH_BYTES:
                    return None
                return z.read(entries[0])
        if path.stat().st_size > MAX_HASH_BYTES:
            return None
        return path.read_bytes()
    except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError):
        return None


def rom_hash(path, platform: str):
    """Hash of a ROM file for RetroAchievements, or None (not a hashable platform, unreadable, drive unmounted)."""
    if platform not in HASH_PLATFORMS:
        return None
    data = read_rom(path)
    return hash_bytes(platform, data) if data else None


def needs_identify(row) -> bool:
    """True when this ROM should be (re)checked against RetroAchievements' hash list."""
    if not (enabled() and row["platform"] in HASH_PLATFORMS):
        return False
    if row["ra_verified"] == 1:
        return False
    if row["ra_verified"] == -1 and row["ra_hash_checked"] and time.time() - row["ra_hash_checked"] < db.RA_MISS_RETRY_SECS:
        return False
    return True


def identify_row(game_id: int) -> Optional[dict]:
    """Settle one ROM's RetroAchievements set by its hash. Worker thread; reads the ROM (on demand) and, at most
    once a week per console, downloads that console's hash list.

    The hash is authoritative: a match replaces whatever the title guessed; no match means RetroAchievements has no
    set for this exact ROM (a translation, a hack, an unlisted dump), so a title-based badge is removed.
    Returns {"changed", "ra_game_id", "verified", "hash"} or None when nothing could be decided (not configured, not a
    cartridge platform, the drive is not mounted, RetroAchievements unreachable): then nothing is changed.
    """
    with db.session() as con:
        row = db.get_game(con, game_id)
    if row is None or not needs_identify(row):
        return None
    digest = row["rom_hash"]
    if not digest:
        if not Path(row["path_or_id"]).exists():
            return None                                  # external drive unmounted: try again another time
        digest = rom_hash(row["path_or_id"], row["platform"])
        if not digest:
            return None
        with db.session() as con:
            db.set_rom_hash(con, game_id, digest)
    cid = console_ids().get(row["platform"])
    if cid is None:
        return None
    hit = hash_index(cid).get(digest)                    # may raise NetworkError: the caller treats that as "later"
    with db.session() as con:
        if hit:
            db.set_ra_verified(con, game_id, hit[0], hit[1])
        else:
            db.set_ra_verified(con, game_id, None)
    if hit:
        try:
            refresh_game(hit[0])                         # progress + hardcore points for the set (best effort)
        except NetworkError:
            pass
    changed = (row["ra_game_id"], row["ra_verified"]) != (hit[0] if hit else None, 1 if hit else -1)
    return {"changed": changed, "ra_game_id": hit[0] if hit else None, "verified": 1 if hit else -1, "hash": digest}
