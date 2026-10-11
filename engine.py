"""wireplay core: scanning coordinator, process launcher, version stacking and platform tabs.

No UI imports here, so both the curses TUI (app.py) and the Qt GUI (gui.py) can share it.
"""
import os
import re
import shlex
import subprocess
import socket
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

import config
import db
import scanners

# (source name, scanners run for it). A source only counts as "scanned" when all of its scanners finish.
SCANNERS = (
    ("steam", (scanners.scan_steam, scanners.scan_steam_shortcuts)),
    ("heroic", (scanners.scan_heroic_epic,)),
    ("minecraft", (scanners.scan_minecraft,)),
    ("roms", (scanners.scan_roms,)),
)


def scan(con):
    """Run every scanner and sync the DB. Returns a list of warnings.

    A scanner that fails is reported and its source is left out of the sync, so its existing rows
    are kept instead of being marked as uninstalled.
    """
    warnings, games, sources = [], [], set()
    known = {db.source_of(r["platform"]) for r in con.execute("SELECT DISTINCT platform FROM games")}
    for source, fns in SCANNERS:
        try:
            found = [g for fn in fns for g in fn()]
        except FileNotFoundError as e:
            # Never installed / never configured is not a problem. Missing while the library has rows from it
            # (an unmounted ROM drive, an uninstalled launcher) is worth a warning; those rows are kept.
            if source in known or (source == "roms" and config.get_roms_dir() is not None):
                warnings.append(f"{source}: not found, skipped ({e})")
        except Exception as e:                                  # one broken scanner must not stop the rest
            warnings.append(f"{source}: scan failed ({type(e).__name__}: {e})")
        else:
            games += found
            sources.add(source)
    db.sync(con, games, sources)
    return warnings


def launch(row):
    """Start row['launch_cmd'] (+ the game's custom_args) fully detached from this process.

    Returns (Popen or None, status string). The Popen is for watching the process (playtime tracking);
    a reaper thread already waits on it, so callers only need poll()/returncode.
    """
    try:
        extra = row["custom_args"] if "custom_args" in row.keys() else ""
        argv = shlex.split(row["launch_cmd"]) + shlex.split(extra or "")
        if not argv:
            raise ValueError("empty launch command")
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,                             # own session: closing the launcher never kills the game
            env={**os.environ, "APPIMAGELAUNCHER_DISABLE": "1"},
        )
    except (OSError, ValueError) as e:
        return None, f"Failed to launch {row['title']}: {e}"
    # Reap the child when it exits so a long-running GUI does not accumulate zombies.
    threading.Thread(target=proc.wait, daemon=True, name=f"reap-{proc.pid}").start()
    if row["is_installed"] == 0:
        return proc, f"Triggered Steam install dialog for {row['title']}"
    return proc, f"Launched {row['title']} [{row['platform']}]"


def game_folder(row):
    """Folder worth opening for a game: ROM folder, Steam install dir, shortcut exe dir, Minecraft instance."""
    plat, pid = row["platform"], row["path_or_id"]
    if plat == "steam":
        if pid.startswith("shortcut_"):
            folder = scanners.shortcut_exe_dir(int(pid.split("_", 1)[1]))
        else:
            folder = scanners.steam_install_dir(pid)
    elif plat == "minecraft":
        folder = Path(pid)
    elif plat == "epic":
        folder = scanners.epic_install_dir(pid)
    else:
        folder = Path(pid).parent
    return folder if folder and folder.is_dir() else None


def open_folder(folder):
    """xdg-open a folder, detached. Returns an error string or None."""
    try:
        proc = subprocess.Popen(["xdg-open", str(folder)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        return f"Could not open {folder}: {e}"
    threading.Thread(target=proc.wait, daemon=True).start()
    return None


def fmt_playtime(minutes):
    """90 -> '1h 30m', 5 -> '5m', 0 -> '0m'."""
    h, m = divmod(int(minutes or 0), 60)
    return f"{h}h {m}m" if h else f"{m}m"


def days_since(ts, now=None):
    """Whole calendar days between ts and now (0 = today)."""
    now = time.time() if now is None else now
    return (time.localtime(now).tm_yday - time.localtime(ts).tm_yday
            if time.localtime(now).tm_year == time.localtime(ts).tm_year else int((now - ts) // 86400))


def fmt_last_played(ts, now=None):
    """Relative for the last month ('today', '3 days ago'), a date after that, 'never' when unset."""
    if not ts:
        return "never"
    days = days_since(ts, now)
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days <= 30:
        return f"{days} days ago"
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def fmt_session_line(start_time, minutes, now=None):
    """One line of the session history: 'Today · 42m', 'Yesterday · 1h 15m', '3 days ago · 20m', '2026-09-02 · 2h 0m'."""
    when = fmt_last_played(start_time, now)
    return f"{when[:1].upper()}{when[1:]} · {fmt_playtime(minutes)}"


RECENT_DAYS = 7


def played_recently(ts, now=None):
    return bool(ts) and (time.time() if now is None else now) - ts < RECENT_DAYS * 86400


def fmt_playtime_badge(minutes):
    """'' under an hour, else '2h' / '15h'."""
    return f"{int(minutes) // 60}h" if minutes and minutes >= 60 else ""


SORT_MODES = ("alpha", "recent", "playtime")


def _col(row, name, default=0):
    """row[name], or `default` for rows (old fixtures, plain dicts) that do not carry the column."""
    try:
        return row[name] or default
    except (KeyError, IndexError):
        return default


def _best_ra(rows):
    """RetroAchievements numbers for a stack: those of its best version (a stack can hold 91 different games)."""
    def progress(r):
        total, unlocked = _col(r, "ra_total"), _col(r, "ra_unlocked")
        return (bool(_col(r, "ra_mastered")) and total > 0, unlocked / total if total else 0.0, unlocked)
    best = max(rows, key=progress, default=None)
    if best is None or _col(best, "ra_total") <= 0:
        return {"ra_total": 0, "ra_unlocked": 0, "ra_mastered": False}
    return {"ra_total": _col(best, "ra_total"), "ra_unlocked": _col(best, "ra_unlocked"),
            "ra_mastered": bool(_col(best, "ra_mastered"))}


def ra_badge(group):
    """(text, is_gold) for the RetroAchievements badge on a card, or None when the game has no achievements."""
    total, unlocked = group.get("ra_total", 0), group.get("ra_unlocked", 0)
    if total <= 0:
        return None
    if group.get("ra_mastered") or unlocked >= total:
        return "🥇 Mastered", True
    return f"🏆 {unlocked}/{total}", False


def quick_match(group, mode):
    """Quick filters: 'recent' = played within RECENT_DAYS; 'never' = installed and never played (the backlog)."""
    if mode == "recent":
        return played_recently(group["last_played"])
    if mode == "never":
        return bool(group["installed"]) and not group["playtime"] and not group["last_played"]
    return True


SHELF_MAX = 8

_LANG_CODES = frozenset("en de fr es it pl pt pt-br ru nl sv ja ko zh tr cs hu fi da no el he ar".split())
_PLAIN_REV = re.compile(r"^(?:rev\.? ?[\w.]+|v ?\d[\w.]*|\d[\w.]*)$", re.I)
VERSION_KINDS = ("hack", "translation", "subset", "homebrew", "demo", "revision")


def version_kinds(row):
    """What sort of version a ROM is, from its [tag] and file name: any of VERSION_KINDS (the browser's #chips)."""
    m = re.search(r"\[([^\]]*)\]\s*$", row["title"] or "")
    tag = m.group(1) if m else ""
    tokens = [t.strip() for t in re.split(r"[,;]", tag) if t.strip()]
    blob = f"{tag} {row['version_label']} {Path(row['path_or_id']).stem}".lower()
    kinds = set()
    if "hack" in blob:
        kinds.add("hack")
    if any(t.lower() in _LANG_CODES for t in tokens) or re.search(r"translat|\(t[+-]|\bt\+", blob):
        kinds.add("translation")
    if "subset" in blob:
        kinds.add("subset")
    if re.search(r"homebrew|unlicensed|\(unl\)", blob):
        kinds.add("homebrew")
    if re.search(r"\bdemo\b|\bproto|\bbeta\b|\bsample\b", blob):
        kinds.add("demo")
    if tokens and all(_PLAIN_REV.match(t) for t in tokens):
        kinds.add("revision")
    return kinds


def resume_row(group):
    """The version to resume: the installed row played most recently (None when nothing was played)."""
    played = [r for r in group["rows"] if (_col(r, "last_played") and r["is_installed"])]
    return max(played, key=lambda r: r["last_played"], default=None)


def shelf_groups(groups, limit=SHELF_MAX):
    """The most recently played installed games, newest first, as copies the shelf can show next to the grid.

    Each copy has its own cache key (so the shelf and the grid never fight over one poster size) and keeps the
    original group under "orig" (what the details drawer shows).
    """
    played = sorted((g for g in groups if g["installed"] and g["last_played"] and resume_row(g)),
                    key=lambda g: g["last_played"], reverse=True)[:limit]
    return [{**g, "key": ("shelf", *g["key"]), "orig": g} for g in played]


def tag_key(tag):
    """Form of a tag used for matching and in `#tag` queries: lower case, no spaces ('Currently Playing' -> 'currentlyplaying')."""
    return "".join(tag.lower().split())


def parse_query(text):
    """Split a search box text into (title terms, tag keys): '#rpg witcher' -> (['witcher'], ['rpg'])."""
    terms, tags = [], []
    for word in text.lower().split():
        if word.startswith("#"):
            if key := tag_key(word.lstrip("#")):
                tags.append(key)                 # a lone '#' (mid-typing) filters nothing
        else:
            terms.append(word)
    return terms, tags


def has_tags(group, wanted, known=frozenset()):
    """True if the group carries every wanted tag key. A key equal to a tag that exists (`known`, the library's
    tag keys) must match exactly, so '#rpg' never pulls in 'rpgmaker'; otherwise it is a prefix, for typing '#rp'."""
    keys = group["tag_keys"]
    return all(any(k == w for k in keys) if w in known else any(k.startswith(w) for k in keys) for w in wanted)


def sort_groups(groups, sort_by="alpha"):
    """New list ordered by 'alpha' (A-Z), 'recent' (last played first) or 'playtime' (most played first)."""
    alpha = lambda g: (g["base"].lower(), g["platform"])               # noqa: E731
    if sort_by == "recent":
        return sorted(groups, key=lambda g: (g["last_played"] == 0, -g["last_played"], *alpha(g)))
    if sort_by == "playtime":
        return sorted(groups, key=lambda g: (-g["playtime"], *alpha(g)))
    return sorted(groups, key=alpha)


def group_games(games, sort_by="alpha"):
    """Stack rows by (platform, base_title, case-insensitive). Returns a sorted list of group dicts."""
    groups = {}
    for g in games:
        groups.setdefault((g["platform"], g["base_title"].lower()), []).append(g)
    out = []
    for key, rows in groups.items():
        platform = key[0]
        # Display name: the most common spelling, ties going to the one with more capitals.
        spellings = [r["base_title"] for r in rows]
        base = max(set(spellings), key=lambda b: (spellings.count(b), sum(c.isupper() for c in b)))
        labels = [r["version_label"] for r in rows]
        # Same label twice (e.g. USA vs Europe dump): tell them apart by file name.
        shown = [
            Path(r["path_or_id"]).stem if labels.count(r["version_label"]) > 1 else r["version_label"]
            for r in rows
        ]
        text = rows[0]["title"] if len(rows) == 1 else f"{base} ({len(rows)} versions ▾)"
        full_text = " ".join([base, platform, *labels, *(r["title"] for r in rows)])
        folded = unicodedata.normalize("NFKD", full_text).encode("ascii", "ignore").decode()
        tags = db.parse_tags([t for r in rows for t in db.parse_tags(r["tags"] if "tags" in r.keys() else "")])
        out.append({
            "key": key, "platform": platform, "base": base, "text": text, "rows": rows, "labels": shown,
            "tags": tags, "tag_keys": [tag_key(t) for t in tags],
            **_best_ra(rows),
            "installed": any(r["is_installed"] == 1 for r in rows),
            "favorite": any(r["is_favorite"] for r in rows),
            "playtime": sum(r["total_playtime_minutes"] for r in rows),
            "last_played": max((r["last_played"] or 0 for r in rows), default=0),
            "haystack": f"{full_text.lower()} {folded.lower()}",
        })
    return sort_groups(out, sort_by)


PRIORITY_TABS = ("steam", "epic", "minecraft")


def tab_names(groups):
    """None ([All]), then steam / epic / minecraft, then the other platforms by game count."""
    counts = {}
    for g in groups:
        counts[g["platform"]] = counts.get(g["platform"], 0) + len(g["rows"])
    plats = sorted(counts, key=lambda p: (PRIORITY_TABS.index(p) if p in PRIORITY_TABS else len(PRIORITY_TABS),
                                          -counts[p], p))
    return [None, *plats]


def tab_label(p):
    return {None: "All", "steam": "Steam", "epic": "Epic", "minecraft": "Minecraft"}.get(p, p)


# ---------------------------------------------------------------- display names (Discord, UI)

PLATFORM_NAMES = {
    "steam": "Steam", "epic": "Epic Games", "minecraft": "Minecraft", "nes": "NES", "snes": "Super Nintendo",
    "n64": "Nintendo 64", "gb": "Game Boy", "gbc": "Game Boy Color", "gba": "Game Boy Advance",
    "nds": "Nintendo DS", "n3ds": "Nintendo 3DS", "gc": "GameCube", "wii": "Wii", "wiiu": "Wii U",
    "switch": "Nintendo Switch", "psx": "PlayStation", "ps2": "PlayStation 2", "ps3": "PlayStation 3",
    "ps4": "PlayStation 4", "psp": "PlayStation Portable", "psvita": "PlayStation Vita",
    "genesis": "Sega Genesis", "megadrive": "Sega Mega Drive", "sega32x": "Sega 32X",
    "dreamcast": "Sega Dreamcast", "atari2600": "Atari 2600", "xbox": "Xbox", "xbox360": "Xbox 360",
}


def platform_name(platform: str) -> str:
    return PLATFORM_NAMES.get(platform, platform)


def presence_title(row) -> str:
    """Game title for Discord: the display title without its [Hack, v1.2] style badges."""
    return re.sub(r"\s*\[[^\]]*\]\s*$", "", row["title"]).strip() or row["title"]


# ---------------------------------------------------------------- what is installed here

def integration_status():
    """{"steam": (ok, text), "heroic": (ok, text)}: what the Settings dialog and the first-run wizard show."""
    out = {}
    steam = config.get_steam_dir()
    if steam and (steam / "steamapps").is_dir():
        out["steam"] = (True, f"✓ Steam found: {steam}")
    else:
        out["steam"] = (False, "✗ Steam not found (install Steam, or set \"steam_dir\" in config.json)")
    heroic = config.get_heroic_dir()
    if heroic and (heroic / "store_cache/legendary_library.json").is_file():
        out["heroic"] = (True, f"✓ Heroic found: {heroic} (Epic library available)")
    elif heroic and heroic.is_dir():
        out["heroic"] = (False, f"• Heroic found: {heroic}, but no Epic library yet (log in to Epic in Heroic)")
    else:
        out["heroic"] = (False, "✗ Heroic Games Launcher not found (optional, for Epic games)")
    return out


# ---------------------------------------------------------------- Syncthing save awareness

SAVE_EXTS = (".srm", ".sav", ".sram", ".eep", ".fla", ".mpk", ".dsv")     # RetroArch / mGBA / Mupen / melonDS saves
SAVE_WALK_DEPTH, SAVE_WALK_LIMIT, SAVE_INDEX_TTL = 4, 20000, 20.0
_syncthing_cache = {"key": None, "folders": []}
_save_index_cache = {}


def syncthing_config_path():
    """Syncthing's config.xml (XDG state dir first, then the older ~/.config location), or None."""
    state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    for p in (state / "syncthing/config.xml", Path.home() / ".local/state/syncthing/config.xml",
              Path.home() / ".config/syncthing/config.xml"):
        if p.is_file():
            return p
    return None


def syncthing_folders():
    """[{"id", "label", "path" (symlinks resolved), "paused", "peers"}] for every folder Syncthing shares.

    Only folder and device-name attributes are read from config.xml; its other contents (the GUI API key
    among them) are never looked at. `peers` are the other devices' names (this machine's own name is left out).
    Cached until the file changes; [] when Syncthing is not set up or the file cannot be parsed.
    """
    path = syncthing_config_path()
    if path is None:
        return []
    try:
        key = (str(path), path.stat().st_mtime_ns)
        if _syncthing_cache["key"] == key:
            return _syncthing_cache["folders"]
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return []
    names = {d.get("id"): d.get("name") or "" for d in root.findall("device")}
    me = socket.gethostname().lower()
    folders = []
    for f in root.findall("folder"):
        raw = f.get("path")
        if not raw:
            continue
        peers = [names.get(d.get("id"), "") for d in f.findall("device")]
        peers = [n for n in peers if n and n.lower() != me]
        try:
            real = Path(raw).expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        folders.append({"id": f.get("id") or "", "label": f.get("label") or f.get("id") or "", "path": real,
                        "paused": (f.get("paused") or "").lower() == "true", "peers": peers})
    _syncthing_cache.update(key=key, folders=folders)
    return folders


def _fold(name):
    return unicodedata.normalize("NFC", name).casefold()


def _save_index(root):
    """{folded file name: [Path]} of the save files under `root` (shallow walk, short-lived cache)."""
    hit = _save_index_cache.get(root)
    if hit and time.monotonic() - hit[0] < SAVE_INDEX_TTL:
        return hit[1]
    index, seen = {}, 0
    base_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        if len(Path(dirpath).parts) - base_depth >= SAVE_WALK_DEPTH:
            dirnames[:] = []
        for fn in filenames:
            seen += 1
            if fn.lower().endswith(SAVE_EXTS):
                index.setdefault(_fold(fn), []).append(Path(dirpath) / fn)
        if seen > SAVE_WALK_LIMIT:
            break
    _save_index_cache[root] = (time.monotonic(), index)
    return index


def _inside(path, folder):
    return path == folder or folder in path.parents


def find_synced_save(row):
    """The Syncthing-shared save file of a ROM game, or None.

    A save counts when a file named like the ROM (<rom name>.srm / .sav / ...) exists inside a Syncthing folder:
    either a "saves" folder (searched by name, any sub-folder such as RetroArch's per-core directories) or the
    folder holding the ROM itself. Returns {"file", "folder", "label", "peers"}; the newest copy wins.
    May touch a slow or unmounted drive: call it from a worker thread.
    """
    if db.source_of(row["platform"]) != "roms":
        return None
    rom = Path(row["path_or_id"])
    folders = [f for f in syncthing_folders() if not f["paused"]]
    if not folders:
        return None
    stem = _fold(rom.stem)
    found = []                                                  # (mtime, file, folder dict)
    for f in folders:
        candidates = []
        if any("save" in part.lower() for part in f["path"].parts):
            for ext in SAVE_EXTS:
                candidates += _save_index(f["path"]).get(stem + ext, [])
        else:
            beside = [rom.with_name(rom.stem + ext) for ext in SAVE_EXTS]
            candidates = [p for p in beside if _inside(p.parent.resolve(), f["path"])]
        for c in candidates:
            try:
                found.append((c.stat().st_mtime, c, f))
            except OSError:
                continue
    if not found:
        return None
    _mtime, file, folder = max(found, key=lambda t: t[0])
    return {"file": file, "folder": file.parent, "label": folder["label"], "peers": folder["peers"]}


def syncthing_badge(sync):
    """'🔄 Syncthing (<device name>)' - the other devices named in Syncthing's config."""
    return "🔄 Syncthing" + (f" ({', '.join(sync['peers'])})" if sync["peers"] else "")


# ---------------------------------------------------------------- play sessions

BEAT_SECS = 30.0                 # how often a running session writes its heartbeat to the DB


class _Tracker:
    """Common state of every play-session tracker (see PopenSession / EnvSession).

    poll(now, taken) -> None while running, else the minutes to record, or -1 to discard (the launch failed at
    once). `now` and `start` are time.monotonic() values; once a session is over `end` is its monotonic end.
    The GUI's SessionWatcher fills in session_id / start_wall (the DB row) and does the heartbeat bookkeeping.
    """
    session_id = 0
    start_wall = 0.0
    end = None
    last_beat = -1e9
    beat_root = None

    def root_info(self):
        """(pid, starttime) of the followed process once known, else None. Persisted so a restart can re-attach."""
        return None


class PopenSession(_Tracker):
    """A session that lasts as long as the launched process (emulators, Minecraft, your own launcher scripts ...).

    With proc=None the process is followed by (pid, starttime) alone: a session resumed after a restart.
    """

    def __init__(self, proc, game_id, now, root=None):
        self.proc, self.game_id, self.start = proc, game_id, now
        if root is None and proc is not None:
            root = (proc.pid, proc_start(proc.pid))
        self.root = root

    def root_info(self):
        return self.root if self.root and self.root[1] is not None else None

    def _running(self):
        if self.proc is not None:
            return self.proc.poll() is None
        return self.root is not None and proc_start(self.root[0]) == self.root[1]

    def pids(self):
        return {self.root[0]} if self.root and self._running() else set()

    def poll(self, now, taken=frozenset()):
        if self._running():
            return None
        self.end = now
        elapsed = now - self.start
        if self.proc is not None and self.proc.poll() != 0 and elapsed < 10:
            return -1
        return round(elapsed / 60)


def steam_env_tokens(row):
    """Byte strings that appear in the environment of a process Steam started for this game, or None.

    Native games carry SteamAppId=<id>; Proton also sets STEAM_COMPAT_DATA_PATH=.../compatdata/<id>.
    Non-Steam shortcuts use their 32-bit shortcut id there and SteamGameId=<64-bit game id>.
    Rows not started through Steam (custom launch commands, ROMs) return None.
    """
    if row["platform"] != "steam" or row["is_installed"] == 0:
        return None
    pid = row["path_or_id"]
    if pid.startswith("shortcut_"):
        if not row["launch_cmd"].startswith("steam "):
            return None
        aid = int(pid.split("_", 1)[1])
        gameid = (aid << 32) | 0x02000000
        return [b"SteamGameId=%d\0" % gameid, b"SteamAppId=%d\0" % aid, b"/compatdata/%d\0" % aid]
    return [b"SteamAppId=%s\0" % pid.encode(), b"SteamGameId=%s\0" % pid.encode(), b"/compatdata/%s\0" % pid.encode()]


def heroic_match(row):
    """(env tokens, install-folder paths) that identify the process of an Epic game started through Heroic, or None.

    Heroic's game wrapper exports HEROIC_APP_NAME=<app name> (checked in Heroic 2.22's app.asar);
    LEGENDARY_APP_NAME is accepted too but is not set by that version. The install folder is matched in the
    command line only (a shell merely sitting in that folder has it in its environment, a game does not).
    Windows games under Wine show it with backslashes, so both spellings are used.
    """
    if row["platform"] != "epic" or row["is_installed"] == 0:
        return None
    app = row["path_or_id"].encode()
    tokens = [b"HEROIC_APP_NAME=%s\0" % app, b"LEGENDARY_APP_NAME=%s\0" % app]
    folder = scanners.epic_install_dir(row["path_or_id"])
    paths = []
    if folder and str(folder) not in ("", "/", "."):
        text = str(folder).rstrip("/")
        paths = [text.encode(), text.replace("/", "\\").encode()]
    return tokens, paths


def proc_start(pid):
    """Start time (clock ticks since boot) of a live, non-zombie process, or None. Guards against pid reuse."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            stat = f.read()
    except OSError:
        return None
    fields = stat[stat.rindex(b")") + 2:].split()      # after "(comm)": state is fields[0], starttime fields[19]
    return None if fields[0] in (b"Z", b"X") else int(fields[19])


# Short-lived helpers that mention a game folder on their command line without being the game.
NOT_A_GAME = frozenset({b"xdg-open", b"dolphin", b"nautilus", b"thunar", b"pcmanfm", b"nemo", b"kate", b"code",
                        b"micro", b"vim", b"less", b"cat", b"grep", b"ls", b"ps", b"pgrep", b"du", b"find",
                        b"ranger", b"yazi", b"mc", b"wireplay"})


def path_regex(paths):
    """Regex finding any of `paths` in a command line as a whole path: not glued to a longer name on either
    side, so '/games/Borderlands' does not match '/games/Borderlands 2' or '/old/games/Borderlands'."""
    alternatives = b"|".join(re.escape(p) for p in paths)
    return re.compile(rb"(?<![\w.\-/\\])(?:" + alternatives + rb")(?=[/\\\x00]|$)")


def find_env_pids(tokens, exclude=frozenset(), paths=()):
    """{pid: starttime} of live processes of this user that belong to a game.

    A process belongs to it when its environment contains any of `tokens`, or (when `paths` is given) when
    its command line mentions any of `paths` and it is not a known file-browsing helper.
    """
    found = {}
    me = os.getpid()
    in_cmdline = path_regex(paths) if paths else None
    for name in os.listdir("/proc"):
        if not name.isdigit() or int(name) in exclude or int(name) == me:
            continue
        try:
            with open(f"/proc/{name}/environ", "rb") as f:
                env = f.read()
        except OSError:                                 # other user's process, or it just exited
            continue
        hit = any(t in env for t in tokens)
        if not hit and in_cmdline:
            try:
                with open(f"/proc/{name}/cmdline", "rb") as f:
                    cmd = f.read()
                with open(f"/proc/{name}/comm", "rb") as f:
                    comm = f.read().strip()
            except OSError:
                continue
            hit = comm not in NOT_A_GAME and in_cmdline.search(cmd) is not None
        if hit:
            start = proc_start(int(name))
            if start is not None:
                found[int(name)] = start
    return found


class EnvSession(_Tracker):
    """Tracks a game another launcher (Steam, Heroic) started on our behalf.

    `steam -applaunch` and `heroic heroic://...` return at once (or are the long-lived launcher itself), so the
    launched Popen says nothing about how long the game runs. Instead, for DISCOVERY_SECS after the launch we
    scan /proc for the process the launcher spawns (matched by its environment, see steam_env_tokens and
    heroic_match), then follow that process. When the followed process exits we look for any other process of
    the same game (a launcher handing over to the real exe, Proton/Wine helpers); the session ends once none is
    left for GRACE_SECS. Elapsed time runs from the launch to the last moment a game process was alive.

    wait_launcher: when the game is never seen, True keeps waiting for the launcher process to exit (Steam:
    ~0 minutes); False gives up after the discovery window (Heroic: the launcher is the long-lived UI).
    proc=None / root=(pid, starttime) resumes a session after a wireplay restart.
    """
    DISCOVERY_SECS = 60.0        # cold-starting Steam plus Proton prefix setup can take well over 30 s
    GRACE_SECS = 5.0

    def __init__(self, proc, game_id, tokens, now, paths=(), discovery=None, wait_launcher=True, root=None):
        self.proc, self.game_id, self.tokens, self.paths, self.start = proc, game_id, tokens, tuple(paths), now
        self.discovery = self.DISCOVERY_SECS if discovery is None else discovery
        self.wait_launcher = wait_launcher
        self.root = root                                # (pid, starttime) being followed
        self.last_alive = now

    def root_info(self):
        return self.root

    def pids(self):
        return {self.root[0]} if self.root else set()

    def _attach(self, now, taken):
        found = find_env_pids(self.tokens, exclude=set(taken) - self.pids(), paths=self.paths)
        if not found:
            return False
        pid = min(found, key=found.get)                 # the oldest process is the launch root
        self.root, self.last_alive = (pid, found[pid]), now
        return True

    def poll(self, now, taken=frozenset()):
        if self.root is None:
            if self._attach(now, taken):
                return None
            if now - self.start < self.discovery:
                return None
            # Never saw the game (it did not start, or the environment does not match): fall back to the
            # launcher's own lifetime, which for `steam -applaunch` is ~0 minutes but still sets last-played.
            self.end = now
            rc = self.proc.poll() if self.proc is not None else 0
            if rc is None and self.wait_launcher:
                self.end = None
                return None                             # the launcher itself is still the best signal we have
            return -1 if rc not in (None, 0) else 0
        if proc_start(self.root[0]) == self.root[1]:
            self.last_alive = now
            return None
        if self._attach(now, taken):                    # the root exited but the game continues in another process
            return None
        if now - self.last_alive >= self.GRACE_SECS:
            self.end = self.last_alive
            return max(0, round((self.last_alive - self.start) / 60))
        return None


SteamSession = EnvSession                                # the name used before Heroic support


def make_session(proc, row, now=None):
    """The right session tracker for a launched row."""
    now = time.monotonic() if now is None else now
    if tokens := steam_env_tokens(row):
        return EnvSession(proc, row["id"], tokens, now)
    if match := heroic_match(row):
        return EnvSession(proc, row["id"], match[0], now, paths=match[1], discovery=HEROIC_DISCOVERY_SECS,
                          wait_launcher=False)
    return PopenSession(proc, row["id"], now)


HEROIC_DISCOVERY_SECS = 120.0    # Heroic may update the game or build a Wine prefix before the game appears


def resume_session(row, sess, now_mono, now_wall):
    """Tracker for a session row left open by an earlier wireplay run, or None when its game is no longer running.

    The followed pid only counts if its start time still matches (pid reuse); an Epic/Steam game that has
    moved to another process is found again by its environment. The tracker's clock is back-dated so the
    session keeps its original start.
    """
    start = now_mono - max(0.0, now_wall - sess["start_time"])
    root = None
    if sess["pid"] and sess["pid_start"] and proc_start(sess["pid"]) == sess["pid_start"]:
        root = (sess["pid"], sess["pid_start"])
    if tokens := steam_env_tokens(row):
        tracker = EnvSession(None, row["id"], tokens, start, root=root)
    elif match := heroic_match(row):
        tracker = EnvSession(None, row["id"], match[0], start, paths=match[1], root=root)
    elif root:
        tracker = PopenSession(None, row["id"], start, root=root)
    else:
        return None
    if root is None and not tracker._attach(now_mono, frozenset()):
        return None
    tracker.last_alive = now_mono                       # alive right now: the clock must not stay at the back-dated start
    tracker.session_id, tracker.start_wall = sess["id"], float(sess["start_time"])
    return tracker


def reconcile_sessions(con, now_wall=None, now_mono=None):
    """Settle sessions left open when wireplay quit or crashed while a game ran. Call once at startup.

    Returns (resumed, closed): `resumed` is a list of (game row, tracker) for games still running (watch them
    again); `closed` is a list of (game id, minutes). A game that is gone is credited up to its last heartbeat
    (BEAT_SECS granularity) and never more than db.MAX_SESSION_MINUTES, so no playtime is lost or invented.
    """
    now_wall = time.time() if now_wall is None else now_wall
    now_mono = time.monotonic() if now_mono is None else now_mono
    resumed, closed = [], []
    for sess in db.open_sessions(con):
        game = db.get_game(con, sess["game_id"])
        if game is None:
            db.discard_session(con, sess["id"])
            continue
        tracker = resume_session(game, sess, now_mono, now_wall)
        if tracker is not None:
            resumed.append((game, tracker))
            continue
        seen = max(sess["last_seen"] or sess["start_time"], sess["start_time"])
        minutes = db.finish_session(con, sess["id"], round((seen - sess["start_time"]) / 60), end_time=seen)
        closed.append((game["id"], minutes))
    return resumed, closed
