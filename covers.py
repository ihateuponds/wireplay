"""Cover art: find, download and cache cover images, and render them to terminal cells with chafa.

Art priority for a game (first hit wins):
    custom cover chosen in the GUI > official Steam poster > SteamGridDB 600x900 poster
    > local EmuDeck cover > libretro-thumbnails box art > Steam header (landscape)
"""
import http.client
import json
import os
import re
import struct
import subprocess
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache
from pathlib import Path

import config
from scanners import epic_art, reorder_article



def media_root():
    """The box-art folder (config "media_dir", else detected next to the ROM folder), or None."""
    return config.get_media_dir()


CACHE_DIR = Path(os.environ.get("WIREPLAY_CACHE") or Path.home() / ".cache/wireplay") / "covers"
USER_AGENT = "wireplay/1"

STEAM_URL = "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"
STEAM_POSTER_URL = "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/library_600x900_2x.jpg"
ART_DIRS = ("covers", "3dboxes")          # local EmuDeck art, checked in this order
ART_EXTS = (".png", ".jpg")
IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp")

LIBRETRO_URL = "https://raw.githubusercontent.com/libretro-thumbnails/{repo}/master/Named_Boxarts/{name}.png"
LIBRETRO_REPO = {
    "snes": "Nintendo_-_Super_Nintendo_Entertainment_System",
    "nes": "Nintendo_-_Nintendo_Entertainment_System",
    "gba": "Nintendo_-_Game_Boy_Advance",
    "n64": "Nintendo_-_Nintendo_64",
    "gb": "Nintendo_-_Game_Boy",
    "gbc": "Nintendo_-_Game_Boy_Color",
    "genesis": "Sega_-_Mega_Drive_-_Genesis",
    "megadrive": "Sega_-_Mega_Drive_-_Genesis",
}
REGIONS = ("USA", "Europe", "Japan", "World")

SGDB_API = "https://www.steamgriddb.com/api/v2"
SGDB_GRIDS = "/grids/game/{id}?dimensions=600x900&types=static&mimes=image/png,image/jpeg"
POKEMON_DEFAULT_URL = "https://cdn2.steamgriddb.com/grid/deaf47d9953d7c0216d9c3aa944fe5ba.png"
POKEMON_DEFAULT = CACHE_DIR / "default_pokemon.png"
# Base-game fallbacks for Pokemon hacks, checked in this order.
POKEMON_BASES = (
    (re.compile(r"emerald", re.I), "Pokémon Emerald Version"),
    (re.compile(r"fire\s?red|\bred\b", re.I), "Pokémon FireRed Version"),
    (re.compile(r"crystal", re.I), "Pokémon Crystal Version"),
    (re.compile(r"ruby", re.I), "Pokémon Ruby Version"),
    (re.compile(r"platinum", re.I), "Pokémon Platinum Version"),
)

MISS_TTL = 30 * 24 * 3600                 # a "no such art" marker is trusted for 30 days, then retried
MAX_IMAGE_BYTES = 12 << 20
MAX_JSON_BYTES = 4 << 20
MAX_IN_FLIGHT = 4                         # concurrent HTTP requests across all threads


# ---------------------------------------------------------------- HTTP layer

class NetworkError(Exception):
    """A transient failure (timeout, DNS, 5xx, rate limit, rejected key). Never record a miss for it."""

    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status = status


_lock = threading.Lock()
_in_flight = threading.Semaphore(MAX_IN_FLIGHT)
_cooldown_until = 0.0                     # monotonic time before which no request is sent (after a 429/5xx)


def _backoff(seconds: float):
    global _cooldown_until
    with _lock:
        _cooldown_until = max(_cooldown_until, time.monotonic() + seconds)


def _wait_cooldown():
    delay = _cooldown_until - time.monotonic()
    if delay > 0:
        time.sleep(min(delay, 30))


def _retry_after(err: urllib.error.HTTPError, default: float) -> float:
    try:
        return min(float(err.headers.get("Retry-After", default)), 30.0)
    except (TypeError, ValueError):
        return default


def http_get(url: str, headers=None, timeout=12, max_bytes=MAX_IMAGE_BYTES, missing=(404, 410), data=None):
    """Body of a GET (a POST when `data` bytes are given), or None when the resource definitively does not
    exist (404/410, other 4xx, too big). `headers` may override the default User-Agent.

    `missing` lists the status codes that mean "not there" (Epic's CDN answers 403 for absent files).
    Raises NetworkError for anything that might work later. 429/5xx are retried once after a shared
    back-off so a burst of fetches slows down instead of hammering a rate-limited API.
    """
    # Epic's CDN names contain spaces; percent-encode anything urllib would reject, keeping existing %XX.
    url = urllib.parse.quote(url, safe=":/?&=#%+,;@~!$'()*-._[]")
    if os.environ.get("WIREPLAY_TEST") and not os.environ.get("WIREPLAY_ALLOW_NET") \
            and urllib.parse.urlsplit(url).hostname not in ("127.0.0.1", "localhost"):
        raise NetworkError("network blocked: WIREPLAY_TEST is set (set WIREPLAY_ALLOW_NET=1 for a live test)")
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})})
    for attempt in (0, 1):
        _wait_cooldown()
        try:
            with _in_flight, urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read(max_bytes + 1)
        except urllib.error.HTTPError as e:
            if e.code in missing:
                return None
            if e.code == 429 or e.code >= 500:
                _backoff(_retry_after(e, 2.0 * (attempt + 1)))
                if attempt == 0:
                    continue
            elif e.code in (401, 403):
                raise NetworkError(f"HTTP {e.code}", e.code) from e
            elif 400 <= e.code < 500:
                return None
            raise NetworkError(f"HTTP {e.code}", e.code) from e
        except (OSError, ValueError, http.client.HTTPException) as e:   # URLError, timeouts, resets, short reads
            raise NetworkError(f"{type(e).__name__}: {e}") from e
        return data if len(data) <= max_bytes else None
    raise NetworkError("retries exhausted")


def _image_ext(data: bytes):
    if data.startswith(b"\x89PNG"):
        return ".png"
    if data.startswith(b"\xff\xd8"):
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def download_image(url: str, timeout=15, missing=(404, 410)):
    """(bytes, extension) for an image URL; None when it is missing or not an image. May raise NetworkError."""
    data = http_get(url, timeout=timeout, missing=missing)
    ext = _image_ext(data) if data else None
    return (data, ext) if ext else None


# ---------------------------------------------------------------- cache files

def store(dest: Path, data: bytes) -> Path:
    """Write atomically (unique temp file + rename) so readers and parallel fetchers never see half a file."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=dest.name + ".", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)                      # our own half-written temp file
        except OSError:
            pass
        raise
    return dest


def _miss_path(stem: str) -> Path:
    return CACHE_DIR / f"{stem}.miss"


def _is_miss(stem: str) -> bool:
    try:
        return time.time() - _miss_path(stem).stat().st_mtime < MISS_TTL
    except OSError:
        return False


def _mark_miss(stem: str):
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _miss_path(stem).touch()
    except OSError:
        pass


def _safe(name: str) -> str:
    return re.sub(r"[^\w .,'()&!-]", "_", name)


def _newest(stem: str):
    """Newest cached image named <stem>.<ext>, or None."""
    found = [p for ext in IMG_EXTS if (p := CACHE_DIR / f"{stem}{ext}").is_file()]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


# ---------------------------------------------------------------- rows

def is_steam_app(row) -> bool:
    return row["platform"] == "steam" and not str(row["path_or_id"]).startswith("shortcut_")


def representatives(rows):
    """A group's versions with 'Default' first, so every lookup agrees on which row stands for the group."""
    return sorted(rows, key=lambda r: (r["version_label"] != "Default", r["version_label"].lower()))


def _as_rows(rows_or_row):
    return representatives([rows_or_row] if hasattr(rows_or_row, "keys") else rows_or_row)


def minecraft_icon(row):
    for name in ("icon.png", "icon.webp", "icon.jpg", "icon.PNG"):
        p = Path(row["path_or_id"]) / name
        if p.is_file():
            return p
    return None


MAX_MIRROR_BYTES = 24 << 20               # box art larger than this is served from the drive, not copied


def _media_mirror(platform: str, art: str, stem: str, ext: str) -> Path:
    """Where the local copy of a drive art file lives (the 'media_' prefix keeps it apart from downloaded art)."""
    return CACHE_DIR / f"media_{platform}_{art}_{_safe(stem)}{ext}"


def _mirror(src: Path, platform: str, art: str, stem: str, ext: str):
    """Make sure the local copy of a drive art file is current and return it, or None if it cannot be made.

    Only ever reads from the drive. The copy keeps the source's modification time, so changed art is re-copied
    (size or mtime differs) and unchanged art is never read twice.
    """
    dest = _media_mirror(platform, art, stem, ext)
    try:
        st = src.stat()
        if dest.is_file():
            d = dest.stat()
            if d.st_size == st.st_size and int(d.st_mtime) == int(st.st_mtime):
                return dest
        if st.st_size > MAX_MIRROR_BYTES:
            return None
        data = src.read_bytes()
        store(dest, data)
        os.utime(dest, (st.st_atime, st.st_mtime))
        return dest
    except OSError:                                   # drive vanished mid-read, disk full ...: use the drive copy
        return None


def rom_cover(platform: str, rom_path: str):
    """Cover from the local EmuDeck media folder, never touching the network.

    Art found on the external drive is copied into ~/.cache/wireplay/covers on first use and the local copy is
    returned from then on; when the drive is not mounted the copy made earlier is served, so the library keeps
    its artwork offline.
    """
    stem, media = Path(rom_path).stem, media_root()
    for art in ART_DIRS if media else ():
        for ext in ART_EXTS:
            src = media / platform / art / f"{stem}{ext}"
            if src.is_file():
                return _mirror(src, platform, art, stem, ext) or src
    for art in ART_DIRS:                              # drive unmounted (or art removed from it): the earlier copy
        for ext in ART_EXTS:
            local = _media_mirror(platform, art, stem, ext)
            if local.is_file():
                return local
    return None


def search_name(row) -> str:
    """Title to look up: the ROM's own name without tags, ' - ' flattened ('Pokemon - Radical Red' -> 'Pokemon Radical Red')."""
    stem = row["title"] if row["platform"] in ("steam", "epic") else Path(row["path_or_id"]).stem
    stem = re.sub(r"[\u2122\u00ae\u00a9]", "", stem)                  # (tm) (R) (c): NFKD would turn (tm) into "TM"
    stem = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", stem).replace("_", " ")
    return reorder_article(re.sub(r"\s+", " ", re.sub(r"\s+-\s+", " ", stem)).strip())


def _norm(text: str) -> list:
    """Lower-case, accent-free word list ('Pokémon R.O.W.E.' -> ['pokemon', 'r', 'o', 'w', 'e'])."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.findall(r"[a-z0-9]+", text)


def is_pokemon(name: str) -> bool:
    return _norm(name)[:1] == ["pokemon"]


def sgdb_cache_stem(row) -> str:
    return f"sgdb_{row['platform']}_{_safe(search_name(row))}"


def sgdb_cached(row):
    return _newest(sgdb_cache_stem(row))


def steam_poster_path(appid: str) -> Path:
    return CACHE_DIR / f"steam_{appid}_poster.jpg"


def steam_header_path(appid: str) -> Path:
    return CACHE_DIR / f"steam_{appid}.jpg"


def custom_stem(row) -> str:
    """Cache name of a user-chosen cover: per Steam app, otherwise per (platform, base title) group."""
    if is_steam_app(row):
        return f"custom_steam_{row['path_or_id']}"
    return f"custom_{row['platform']}_{_safe(row['base_title'].lower())}"


def retro_stem(platform: str, base_title: str) -> str:
    return f"{platform}_{_safe(base_title)}"


# ---------------------------------------------------------------- libretro-thumbnails

def _libretro_safe(name: str) -> str:
    """libretro-thumbnails replaces these characters with '_' in file names."""
    return re.sub(r'[&*/:`<>?\\|"]', "_", name)


def retro_candidates(rows):
    """Likely libretro thumbnail names for a group: ROM file names first, then '<base> (<region>)'."""
    ordered = representatives(rows)
    names = [Path(r["path_or_id"]).stem for r in ordered[:3]]
    base = ordered[0]["base_title"]
    names += [f"{base} ({reg})" for reg in REGIONS]
    m = re.match(r"^(The|A|An) (.+)$", base)              # libretro files 'Legend of Zelda, The'
    if m:
        names += [f"{m.group(2)}, {m.group(1)} ({reg})" for reg in REGIONS]
    return list(dict.fromkeys(_libretro_safe(n) for n in names))[:10]


def fetch_retro_cover(rows):
    """Try each candidate name against libretro-thumbnails (blocking). Returns the cached path or None."""
    first = representatives(rows)[0]
    stem = retro_stem(first["platform"], first["base_title"])
    dest = CACHE_DIR / f"{stem}.png"
    if dest.is_file():
        return dest
    if _is_miss(stem):
        return None
    repo = LIBRETRO_REPO[first["platform"]]
    try:
        for name in retro_candidates(rows):
            data = http_get(LIBRETRO_URL.format(repo=repo, name=urllib.parse.quote(name)), timeout=10)
            if data and data.startswith(b"\x89PNG"):
                return store(dest, data)
    except NetworkError:
        return None                                       # transient: no miss recorded, retried next time
    _mark_miss(stem)
    return None


# ---------------------------------------------------------------- SteamGridDB

_sgdb = {"key": None, "loaded": False, "dead": False}     # dead: the API rejected our key this session
_search_cache = {}


def reload_api_key():
    """Forget the cached SteamGridDB key so the next request re-reads config.json (after Settings changed it)."""
    with _lock:
        _sgdb.update(loaded=False, key=None, dead=False)


def api_key():
    """steamgriddb_api_key from ~/.config/wireplay/config.json, or None. Never logged or printed."""
    with _lock:
        if not _sgdb["loaded"]:
            _sgdb["loaded"] = True
            try:
                key = config.load().get("steamgriddb_api_key")
                _sgdb["key"] = key.strip() if isinstance(key, str) and key.strip() else None
            except AttributeError:
                _sgdb["key"] = None
        return None if _sgdb["dead"] else _sgdb["key"]


def _sgdb_get(path: str):
    """GET a SteamGridDB API path -> its 'data' list. None = no such thing; NetworkError = try again later."""
    key = api_key()
    if not key:
        raise NetworkError("no SteamGridDB API key")
    try:
        body = http_get(SGDB_API + path, headers={"Authorization": f"Bearer {key}"}, timeout=12,
                        max_bytes=MAX_JSON_BYTES)
    except NetworkError as e:
        if e.status in (401, 403):
            with _lock:
                _sgdb["dead"] = True                      # bad key: stop hammering the API
        raise
    try:
        doc = json.loads(body) if body else None
    except ValueError as e:
        raise NetworkError(f"bad JSON from SteamGridDB: {e}") from e
    return doc.get("data") if isinstance(doc, dict) and doc.get("success") else None


def sgdb_search(term: str):
    """Best SteamGridDB game id for a title, or None.

    Autocomplete is fuzzy and will happily answer a hack name with an unrelated game, so a result only
    counts when its name contains every word of the query. Exact name beats shortest name.
    """
    words = _norm(term)
    if not words:
        return None
    with _lock:
        if term in _search_cache:
            return _search_cache[term]
    found = None
    data = _sgdb_get("/search/autocomplete/" + urllib.parse.quote(term, safe=""))
    if data:
        ok = [g for g in data if set(words) <= set(_norm(g.get("name", "")))]
        if not ok:
            spaceless = "".join(words)
            ok = [g for g in data if spaceless == "".join(_norm(g.get("name", "")))]
        exact = [g for g in ok if _norm(g["name"]) == words]
        pick = exact or sorted(ok, key=lambda g: len(g["name"]))
        found = pick[0]["id"] if pick else None
    with _lock:                                           # only definitive answers get here
        _search_cache[term] = found
    return found


def sgdb_grids(game_id):
    """600x900 static grids for a game, best first: [{'url': ..., 'thumb': ...}, ...]."""
    out = []
    for item in _sgdb_get(SGDB_GRIDS.format(id=game_id)) or ():
        if isinstance(item, dict) and item.get("url"):
            out.append({"url": item["url"], "thumb": item.get("thumb") or item["url"]})
    return out


_VERSION_WORDS = re.compile(r"\b(version|edition|rev\s?\w*|v\d+[\d\.]*)\b", re.I)
_NUMERALS = (("2", "II"), ("3", "III"), ("4", "IV"), ("5", "V"), ("6", "VI"))


def sgdb_candidates(name: str, row=None):
    """Search terms for a title, best first. Pokemon: full name, then a base-game keyword; never bare 'Pokemon'."""
    terms = [name]

    def strip_version(text):
        return re.sub(r"\s+", " ", _VERSION_WORDS.sub("", text)).strip(" -:")

    if strip_version(name) != name:
        terms.append(strip_version(name))
    base = row["base_title"] if row else ""
    if base:
        terms += [base, strip_version(base)]

    for t in list(terms):
        for arab, roman in _NUMERALS:
            if re.search(rf"\b{arab}\b", t):
                terms.append(re.sub(rf"\b{arab}\b", roman, t))
            if re.search(rf"\b{roman}\b", t, flags=re.I):
                terms.append(re.sub(rf"\b{roman}\b", arab, t, flags=re.I))

    if is_pokemon(name):
        terms += [game for pattern, game in POKEMON_BASES if pattern.search(name)]
        terms = [t for t in terms if _norm(t) != ["pokemon"]]   # the bare word returns Pokemon Pinball

    seen = {}
    for t in terms:
        t = t.strip()
        if t:
            seen.setdefault(t.lower(), t)
    return list(seen.values())


def _pokemon_default():
    """The curated generic vertical Pokemon poster (downloaded once). None if it cannot be fetched now."""
    if POKEMON_DEFAULT.is_file():
        return POKEMON_DEFAULT
    try:
        got = download_image(POKEMON_DEFAULT_URL)
    except NetworkError:
        return None
    return store(POKEMON_DEFAULT, got[0]) if got and got[1] == ".png" else None


def fetch_sgdb_poster(row):
    """Download the top 600x900 SteamGridDB poster for one game into the cache (blocking).

    Returns the path; or, for a Pokemon title with no match, the generic Pokemon poster; or None.
    A "not found" is remembered (for MISS_TTL); a network or API failure is not.
    """
    if not api_key():
        return None
    cached = sgdb_cached(row)
    if cached:
        return cached
    stem, name = sgdb_cache_stem(row), search_name(row)
    if not _is_miss(stem):
        try:
            for term in sgdb_candidates(name, row):
                gid = sgdb_search(term)
                grids = sgdb_grids(gid) if gid else ()
                got = download_image(grids[0]["url"]) if grids else None
                if got:
                    return store(CACHE_DIR / f"{stem}{got[1]}", got[0])
        except NetworkError:
            return None
        _mark_miss(stem)
    return _pokemon_default() if is_pokemon(name) else None


def sgdb_fetch_alternatives(name: str, row=None, limit: int = 15):
    """Alternative 600x900 posters for the cover picker: [{'url', 'thumb'}, ...]. Raises NetworkError."""
    for term in sgdb_candidates(name, row):
        gid = sgdb_search(term)
        if gid:
            grids = sgdb_grids(gid)
            if grids:
                return grids[:limit]
    return []


def save_custom_cover(rows, img_url: str):
    """Download img_url and make it the cover of the whole group. Returns the cached path or None."""
    got = download_image(img_url)
    if not got:
        return None
    return store(CACHE_DIR / f"{custom_stem(_as_rows(rows)[0])}{got[1]}", got[0])


# ---------------------------------------------------------------- Epic art (Heroic's library cache)

def epic_stem(app_name: str, landscape=False) -> str:
    return f"epic_{app_name}_cover" if landscape else f"epic_{app_name}"


def fetch_epic_art(app_name: str, landscape=False):
    """Epic CDN image (blocking). Portrait `art_square` by default; `landscape=True` gets the wide `art_cover`.

    Heroic's `art_cover` is a 16:9 banner for almost every game; the vertical poster is `art_square`.
    Cached as epic_<app_name>.<ext>; a 404 is remembered, a network failure is not.
    """
    stem = epic_stem(app_name, landscape)
    if p := _newest(stem):
        return p
    if _is_miss(stem):
        return None
    portrait_url, wide_url = epic_art(app_name)
    url = wide_url if landscape else portrait_url
    if not url:
        return None
    try:
        got = download_image(url, missing=(403, 404, 410))
    except NetworkError:
        return None
    if got:
        return store(CACHE_DIR / f"{stem}{got[1]}", got[0])
    _mark_miss(stem)
    return None


# ---------------------------------------------------------------- Steam art

def fetch_steam_poster(appid: str):
    """Official 600x900 library poster (blocking). None when the app has none or the network failed."""
    dest, stem = steam_poster_path(appid), f"steam_{appid}_poster"
    if dest.is_file():
        return dest
    if _is_miss(stem):
        return None
    try:
        data = http_get(STEAM_POSTER_URL.format(appid=appid), timeout=10)
    except NetworkError:
        return None
    if data and data.startswith(b"\xff\xd8"):
        return store(dest, data)
    _mark_miss(stem)
    return None


def fetch_steam_cover(appid: str):
    """The landscape header.jpg (blocking). Returns the path or None."""
    dest = steam_header_path(appid)
    if dest.is_file():
        return dest
    try:
        data = http_get(STEAM_URL.format(appid=appid), timeout=10)
    except NetworkError:
        return None
    return store(dest, data) if data and data.startswith(b"\xff\xd8") else None


# ---------------------------------------------------------------- resolving art for a group

def find_cover(rows_or_row):
    """Best art that is already on disk for a game or a group's rows, or None. Never touches the network."""
    rows = _as_rows(rows_or_row)
    first = rows[0]
    if p := _newest(custom_stem(first)):
        return p
    if is_steam_app(first):
        appid = first["path_or_id"]
        for p in (steam_poster_path(appid), sgdb_cached(first), steam_header_path(appid)):
            if p and p.is_file():
                return p
        return None
    if first["platform"] == "epic":
        aid = first["path_or_id"]
        return _newest(epic_stem(aid)) or sgdb_cached(first) or _newest(epic_stem(aid, landscape=True))
    if first["platform"] == "minecraft" and (p := minecraft_icon(first)):
        return p
    for r in rows:
        if p := sgdb_cached(r):
            return p
    if is_pokemon(first["base_title"]) and POKEMON_DEFAULT.is_file():
        return POKEMON_DEFAULT
    for r in rows:
        if p := rom_cover(r["platform"], r["path_or_id"]):
            return p
    if first["platform"] in LIBRETRO_REPO:
        p = CACHE_DIR / f"{retro_stem(first['platform'], first['base_title'])}.png"
        return p if p.is_file() else None
    return None


def fetch_poster(rows):
    """Find or download portrait art for a group (blocking; call from a worker thread).

    Unlike find_cover this goes to the network for the higher-priority sources before settling for
    lower-priority art that happens to be cached: SteamGridDB is tried before local/libretro art.
    """
    rows = representatives(rows)
    first = rows[0]
    if p := _newest(custom_stem(first)):
        return p
    if is_steam_app(first):
        appid = first["path_or_id"]
        return (fetch_steam_poster(appid) or sgdb_cached(first) or fetch_sgdb_poster(first)
                or fetch_steam_cover(appid))
    if first["platform"] == "epic":                       # Epic portrait > SteamGridDB > Epic landscape banner
        aid = first["path_or_id"]
        return fetch_epic_art(aid) or fetch_sgdb_poster(first) or fetch_epic_art(aid, landscape=True)
    if first["platform"] == "minecraft" and (p := minecraft_icon(first)):
        return p
    for r in rows:
        if p := sgdb_cached(r):
            return p
    if is_pokemon(first["base_title"]):                   # a collection card: the generic poster
        return _pokemon_default()
    if api_key():
        for r in rows[:3]:                                # a group card shows one poster: a few tries is enough
            if p := fetch_sgdb_poster(r):
                return p
    for r in rows:
        if p := rom_cover(r["platform"], r["path_or_id"]):
            return p
    if first["platform"] in LIBRETRO_REPO:
        return fetch_retro_cover(rows)
    return None


# ---------------------------------------------------------------- background fetching (TUI)

_pending, _failed = set(), set()          # fetch keys in flight / that produced nothing


def fetch_key(rows):
    """Key for a background cover fetch, or None when this game has no remote source."""
    first = rows[0]
    if is_steam_app(first):
        return f"steam:{first['path_or_id']}"
    if first["platform"] == "epic":
        return f"epic:{first['path_or_id']}"
    if first["platform"] in LIBRETRO_REPO or api_key():
        return f"{first['platform']}:{first['base_title']}"
    return None


def start_fetch(rows):
    """Fetch a missing cover in a daemon thread, once per game per session."""
    key = fetch_key(rows)
    if key is None:
        return
    with _lock:
        if key in _pending or key in _failed:
            return
        _pending.add(key)

    def work():
        try:
            ok = fetch_poster(rows)
        except Exception:                                 # never leave the key pending forever
            ok = None
        with _lock:
            _pending.discard(key)
            if not ok:
                _failed.add(key)

    threading.Thread(target=work, daemon=True, name="cover-fetch").start()


def fetch_state(rows):
    """'pending', 'failed' or None."""
    key = fetch_key(rows)
    with _lock:
        return "pending" if key in _pending else "failed" if key in _failed else None


def any_pending() -> bool:
    with _lock:
        return bool(_pending)


# ---------------------------------------------------------------- terminal rendering (chafa)

def image_size(path):
    """(width, height) of a PNG or JPEG from its header, or None."""
    try:
        with open(path, "rb") as f:
            head = f.read(26)
            if head.startswith(b"\x89PNG"):
                return struct.unpack(">II", head[16:24])
            if head.startswith(b"\xff\xd8"):
                f.seek(2)
                while True:
                    marker = f.read(4)
                    if len(marker) < 4 or marker[0] != 0xFF:
                        return None
                    if marker[1] in (0xC0, 0xC1, 0xC2):
                        h, w = struct.unpack(">HH", f.read(5)[1:5])
                        return w, h
                    f.seek(struct.unpack(">H", marker[2:4])[0] - 2, 1)
    except (OSError, struct.error):
        pass
    return None


def box_size(path, avail_w, avail_h):
    """Cells to render into. Cells are ~1:2, so a pixel-square vhalf image is W cells x W/2 rows.

    Portrait box art gets a 3:4 box (w = 1.5 * h cells); landscape art fills the width at its own ratio.
    """
    size = image_size(path)
    ratio = (size[0] / size[1]) if size else 0.75
    if ratio >= 1:                                         # landscape (Steam header, some SNES boxes)
        w = avail_w
        h = max(2, round(w / ratio / 2))
        if h > avail_h:
            h, w = avail_h, round(avail_h * 2 * ratio)
    else:
        h = avail_h
        w = round(h * 2 * 0.75)
        if w > avail_w:
            w, h = avail_w, max(2, round(avail_w / 1.5))
    return max(4, w), max(2, h)


_SGR = re.compile(r"\x1b\[([0-9;?]*)([A-Za-z])")


def _parse_ansi(text: str):
    """chafa output -> rows of (text, fg, bg) segments; fg/bg are 256-colour indexes or -1 (default)."""
    rows = []
    for line in text.split("\n"):
        segs, fg, bg, pos = [], -1, -1, 0
        for m in _SGR.finditer(line):
            if m.start() > pos:
                segs.append((line[pos:m.start()], fg, bg))
            pos = m.end()
            if m.group(2) != "m":                     # cursor show/hide etc.
                continue
            p = [int(x) if x else 0 for x in m.group(1).split(";")] or [0]
            i = 0
            while i < len(p):
                if p[i] == 0:
                    fg = bg = -1
                elif p[i] in (38, 48) and i + 2 < len(p) and p[i + 1] == 5:
                    if p[i] == 38:
                        fg = p[i + 2]
                    else:
                        bg = p[i + 2]
                    i += 2
                elif p[i] == 39:
                    fg = -1
                elif p[i] == 49:
                    bg = -1
                i += 1
        if pos < len(line):
            segs.append((line[pos:], fg, bg))
        if segs:
            rows.append(segs)
    return rows


@lru_cache(maxsize=64)
def render(img_path: str, width: int, height: int):
    """Render an image to at most width x height cells. Returns rows of (text, fg, bg), or None on failure."""
    if width < 4 or height < 2:
        return None
    try:
        out = subprocess.run(
            ["chafa", "-f", "symbols", "--symbols=vhalf", f"--size={width}x{height}",
             "--colors=256", "--scale=max", "--dither=none", "--animate=off", img_path],
            capture_output=True, text=True, timeout=5, check=True,
            # chafa probes the controlling tty for colours and would wait on (or steal) curses' input:
            # give it no terminal at all.
            stdin=subprocess.DEVNULL, start_new_session=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return _parse_ansi(out) or None
