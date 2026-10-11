import json
import os
import re
import shlex
import struct
from pathlib import Path
from typing import Optional

import config
from db import DEFAULT_LABEL, Game


def steam_root():
    """Steam's data folder (config "steam_dir", else detected: native, ~/.steam symlink, Flatpak). May not exist."""
    return config.get_steam_dir() or Path.home() / ".local/share/Steam"


def steam_apps():
    return steam_root() / "steamapps"


def appinfo_path():
    return steam_root() / "appcache/appinfo.vdf"


def roms_root():
    """The ROM library folder (config "roms_dir", else detected), or None when there is none to look in."""
    return config.get_roms_dir()


def emu_scripts():
    """EmuDeck's launcher scripts, in <roms>/emulators; None without a ROM folder."""
    root = roms_root()
    return root / "emulators" if root else None


GDLAUNCHER_GLOB = "GDLauncher*.appimage"
GDLAUNCHER_DIR = Path.home() / "Applications"
GDLAUNCHER_INSTANCES = Path.home() / ".local/share/gdlauncher_carbon/data/instances"

# Steam entries that are tooling, not games.
STEAM_SKIP = re.compile(r"^(Proton\b|Steam Linux Runtime|Steamworks|Dedicated Server|Authoring Tools|SDK|Steam Client|winui|Steam Cloud|Steam Screenshots)", re.I)
STEAM_SKIP_TYPES = {"config", "tool"}      # appinfo common/type values that are never games
SHORTCUTS_SKIP = re.compile(r"^(RetroArch|Dolphin|PCSX2|RPCS3|PPSSPP|Cemu|Ryujinx|xemu|DuckStation|PrimeHack|EmulationStation|Heroic|Sober|GDLauncher)", re.I)


def heroic_library():
    """Heroic's cached Epic library file (config "heroic_dir", else detected native/Flatpak)."""
    base = config.get_heroic_dir() or Path.home() / ".config/heroic"
    return base / "store_cache/legendary_library.json"


HEROIC_LAUNCH = "heroic heroic://launch/legendary/{app_name}"   # for an uninstalled game Heroic offers to install it
EPIC_SKIP_DLC = True        # library entries flagged install.is_dlc are add-on packs, not launchable games

ROM_EXTS = {
    ".nes", ".sfc", ".smc", ".fig", ".swc", ".bs", ".fds", ".gb", ".gbc", ".gba", ".nds", ".3ds",
    ".z64", ".n64", ".v64", ".ndd", ".gcm", ".iso", ".chd", ".cue", ".rvz", ".wbfs", ".wad",
    ".gcz", ".nsp", ".xci", ".cia", ".cci", ".wua", ".wux", ".wud", ".pkg", ".cso", ".pbp",
    ".md", ".gen", ".smd", ".sms", ".gg", ".32x", ".pce", ".a26", ".a78", ".lnx", ".zip", ".7z",
}
# Folders inside a platform dir that hold extras rather than games.
ROM_SKIP_DIRS = {"media", "images", "manuals", "videos", "downloaded_media", "dlc n updates"}

# platform dir -> EmuDeck launcher script in roms/emulators. Platforms not listed
# (see RETRO_CORE_FOR for the RetroArch ones) fall back to xdg-open.
EMU_FOR = {
    "ps2": "pcsx2-qt.sh", "psx": "duckstation.sh", "psp": "ppsspp.sh", "ps3": "rpcs3.sh",
    "ps4": "shadps4.sh", "psvita": "vita3k.sh", "gc": "dolphin-emu.sh", "wii": "dolphin-emu.sh",
    "wiiu": "cemu.sh", "switch": "ryujinx.sh", "n3ds": "azahar.sh", "nds": "melonds.sh",
    "gb": "mgba.sh", "gbc": "mgba.sh", "gba": "mgba.sh", "xbox": "xemu-emu.sh",
    "xbox360": "xenia.sh", "dreamcast": "flycast.sh", "model2": "model-2-emulator.sh",
    "model3": "supermodel.sh",
}

# platform dir -> libretro core in the Flatpak RetroArch config.
RETROARCH_CORES = Path.home() / ".var/app/org.libretro.RetroArch/config/retroarch/cores"
RETRO_CORE_FOR = {
    "snes": "snes9x_libretro.so", "nes": "nestopia_libretro.so",
    "n64": "mupen64plus_next_libretro.so", "genesis": "genesis_plus_gx_libretro.so",
    "megadrive": "genesis_plus_gx_libretro.so", "sega32x": "picodrive_libretro.so",
    "atari2600": "stella_libretro.so",
}

_TAG = re.compile(r"[\(\[]([^\)\]]*)[\)\]]")
_ARTICLE = re.compile(r"^(.*?),\s*(The|A|An)(\s+-\s+.*)?$")
# Tags worth showing as a badge; every other tag (regions, "En,Fr,De", authors, ...) is dropped.
_KEEP_TAG = re.compile(
    r"\bSubset\b|hack|aftermarket|randomi[sz]er|edition|^v\d|^Rev\s?\w+$|^new controls$"
    r"|^proto\b|^beta\b|^demo\b|^T-[A-Za-z]{2}\b",
    re.I,
)
# A lone non-English language code, e.g. (Fr) or (Pt), marks a translation. "En" is the default.
_TRANSLATION = re.compile(r"^(?!En$)[A-Z][a-z]$")


def _keep_tag(tag: str) -> bool:
    return bool(_TRANSLATION.match(tag) or _KEEP_TAG.search(tag))


_HACKISH = re.compile(r"hack|edition|aftermarket|randomi[sz]er", re.I)
_POKEMON = re.compile(r"^Pok[eé]mon\b[\s_]*(-\s*)?", re.I)


def reorder_article(name: str) -> str:
    """'Legend of Zelda, The - Twilight Princess' -> 'The Legend of Zelda - Twilight Princess'."""
    m = _ARTICLE.match(name)
    return f"{m.group(2)} {m.group(1)}{m.group(3) or ''}" if m else name


def parse_title(filename: str):
    """Split a ROM file name into (base_title, version_label, display_title).

    'Legend of Zelda, The - Twilight Princess (USA) (Rev 1).rvz'
        -> ('The Legend of Zelda', 'Twilight Princess, Rev 1', 'The Legend of Zelda - Twilight Princess [Rev 1]')

    Names with a hack/edition/aftermarket tag, or with " - ", split on the first " - ": the part before
    it is the base title (so hacks stack under their parent game) and the subtitle joins the label.
    All Pokemon titles collapse under base_title = 'Pokémon' into a single collection.
    """
    stem = Path(filename).stem
    badges = []
    for tag in _TAG.findall(stem):
        tag = tag.strip()
        if _keep_tag(tag) and tag not in badges:
            badges.append(tag)
    name = _TAG.sub("", stem).replace("_", " ")
    name = re.sub(r"(\s*-\s+){2,}", " - ", name)          # "EarthBound - - Holiday Hex"
    name = re.sub(r"\s+", " ", name).strip(" -")
    name = reorder_article(name) or stem
    display = f"{name} [{', '.join(badges)}]" if badges else name

    m_poke = _POKEMON.match(name)
    if m_poke:
        base = "Pokémon"
        sub = name[m_poke.end():].strip(" -")
        label = ", ".join(p for p in [sub.strip(), *badges] if p) or DEFAULT_LABEL
        return base, label, display

    base, sub = name, ""
    if " - " in name or any(_HACKISH.search(b) for b in badges):
        base, _, sub = name.partition(" - ")
    label = ", ".join(p for p in [sub.strip(), *badges] if p) or DEFAULT_LABEL
    return base.strip() or stem, label, display


def clean_title(filename: str) -> str:
    return parse_title(filename)[2]


# ---------------------------------------------------------------- Valve data formats

class VdfError(ValueError):
    """A VDF file is truncated, corrupt or not the format we expected."""


_MAX_DEPTH = 32
_TEXT_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')


def parse_text_vdf(text: str) -> dict:
    """Valve text KeyValues -> nested dict with lower-cased keys. Raises VdfError on unbalanced braces."""
    root, stack, key = {}, [], None
    cur = root
    for m in _TEXT_TOKEN.finditer(text):
        word, brace = m.groups()
        if brace == "{":
            if key is None:
                raise VdfError("'{' without a key")
            child = {}
            cur[key] = child
            stack.append(cur)
            cur, key = child, None
            if len(stack) > _MAX_DEPTH:
                raise VdfError("nesting too deep")
        elif brace == "}":
            if not stack:
                raise VdfError("unbalanced '}'")
            cur, key = stack.pop(), None
        elif key is None:
            key = word.lower()
        else:
            cur[key], key = word, None
    if stack:
        raise VdfError("unclosed '{'")
    return root


def parse_binary_vdf(data: bytes, pos: int = 0, strings=None, _depth: int = 0):
    """Valve binary KeyValues -> (dict with lower-cased keys, end position).

    `strings` is the shared key table of appinfo.vdf v41 (keys are u32 indexes into it); without it
    keys are inline NUL-terminated strings (shortcuts.vdf). Every read is bounds-checked and any
    problem is reported as VdfError, never IndexError/struct.error.
    """
    if _depth > _MAX_DEPTH:
        raise VdfError("nesting too deep")
    out = {}
    try:
        while True:
            kind = data[pos]
            pos += 1
            if kind == 0x08:                                   # end of this map
                return out, pos
            if strings is None:
                end = data.index(b"\0", pos)
                key = data[pos:end].decode("utf-8", "replace")
                pos = end + 1
            else:
                key = strings[struct.unpack_from("<I", data, pos)[0]]
                pos += 4
            if kind == 0x00:
                value, pos = parse_binary_vdf(data, pos, strings, _depth + 1)
            elif kind == 0x01:
                end = data.index(b"\0", pos)
                value = data[pos:end].decode("utf-8", "replace")
                pos = end + 1
            elif kind in (0x02, 0x03, 0x04, 0x06):             # int32 / float / pointer / colour
                value = struct.unpack_from("<I", data, pos)[0]
                pos += 4
            elif kind in (0x07, 0x0A):                         # uint64 / int64
                value = struct.unpack_from("<Q", data, pos)[0]
                pos += 8
            else:
                raise VdfError(f"unknown value type 0x{kind:02x} at {pos - 1}")
            out[key.lower()] = value
    except (IndexError, ValueError, struct.error) as e:
        if isinstance(e, VdfError):
            raise
        raise VdfError(f"corrupt data near byte {pos}: {e}") from e


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def read_appinfo(wanted: set) -> dict:
    """{appid(str): (name, type)} for the wanted app ids, from Steam's binary appinfo.vdf (v39-v41).

    Entries are length-prefixed, so apps we do not care about are skipped without parsing. A corrupt
    entry is skipped; a corrupt header or truncated file returns what was read so far.
    """
    data = appinfo_path().read_bytes()
    if len(data) < 16:
        raise VdfError("appinfo.vdf too short")
    magic = struct.unpack_from("<I", data, 0)[0]
    version = magic & 0xFF
    if magic >> 8 != 0x075644 or version not in (39, 40, 41):
        raise VdfError(f"unsupported appinfo.vdf magic 0x{magic:08x}")
    strings, pos, stop = None, 8, len(data)
    if version >= 41:
        table = struct.unpack_from("<q", data, 8)[0]
        if not 16 <= table < len(data) - 4:
            raise VdfError("bad appinfo string table offset")
        count = struct.unpack_from("<I", data, table)[0]
        strings = [s.decode("utf-8", "replace") for s in data[table + 4:].split(b"\0")[:count]]
        pos, stop = 16, table
    skip = 60 if version >= 40 else 40                        # fixed per-entry header before the KeyValues
    found = {}
    while pos + 8 <= stop:
        appid, size = struct.unpack_from("<II", data, pos)
        if appid == 0 or size < skip or pos + 8 + size > stop:
            break
        if appid in wanted:
            try:
                kv, _ = parse_binary_vdf(data, pos + 8 + skip, strings)
                common = kv.get("appinfo", {}).get("common", {})
                name = common.get("name")
                if isinstance(name, str) and name:
                    found[str(appid)] = (name, str(common.get("type", "")))
            except VdfError:
                pass
        pos += 8 + size
    return found


# ---------------------------------------------------------------- Steam

def _library_apps_dirs():
    dirs = [steam_apps()]
    try:
        for p in re.findall(r'"path"\s+"([^"]+)"', _read_text(steam_apps() / "libraryfolders.vdf")):
            d = Path(p) / "steamapps"
            if d.is_dir() and d not in dirs:
                dirs.append(d)
    except OSError:
        pass
    return dirs


def _steam_entry(title, appid, installed):
    return Game(title, "steam", appid, f"steam -applaunch {appid}", "steam", title, DEFAULT_LABEL, installed)


def _installed_steam():
    for apps in _library_apps_dirs():
        for acf in sorted(apps.glob("appmanifest_*.acf")):
            try:
                text = _read_text(acf)
            except OSError:
                continue                                       # being written by Steam / unreadable
            appid = re.search(r'"appid"\s+"(\d+)"', text)
            name = re.search(r'"name"\s+"([^"]*)"', text)
            if appid and name and not STEAM_SKIP.search(name.group(1)):
                yield appid.group(1), name.group(1)


def _owned_steam_ids():
    """App ids Steam has in localconfig.vdf (every account on this machine)."""
    owned = set()
    for path in steam_root().glob("userdata/*/config/localconfig.vdf"):
        try:
            tree = parse_text_vdf(_read_text(path))
        except (OSError, VdfError):
            continue
        node = tree.get("userlocalconfigstore", {})
        for part in ("software", "valve", "steam", "apps"):
            node = node.get(part, {}) if isinstance(node, dict) else {}
        owned.update(k for k, v in node.items() if k.isdigit() and isinstance(v, dict))
    return owned


def steam_install_dir(appid: str):
    """Install folder of an installed Steam app (from its appmanifest), or None."""
    for apps in _library_apps_dirs():
        try:
            text = _read_text(apps / f"appmanifest_{appid}.acf")
        except OSError:
            continue
        m = re.search(r'"installdir"\s+"([^"]+)"', text)
        if m and (apps / "common" / m.group(1)).is_dir():
            return apps / "common" / m.group(1)
    return None


def shortcut_exe_dir(appid: int):
    """Folder of a non-Steam shortcut's executable (from shortcuts.vdf), or None."""
    for f in sorted(steam_root().glob("userdata/*/config/shortcuts.vdf")):
        try:
            tree, _ = parse_binary_vdf(f.read_bytes())
        except (OSError, VdfError):
            continue
        for entry in tree.get("shortcuts", {}).values():
            if isinstance(entry, dict) and entry.get("appid") == appid and isinstance(entry.get("exe"), str):
                return Path(entry["exe"].strip('"')).parent
    return None


def scan_steam():
    """Yield Game for installed Steam apps, then for owned-but-uninstalled games (is_installed=0)."""
    installed_ids = set()
    for appid, name in _installed_steam():
        if appid not in installed_ids:
            installed_ids.add(appid)
            yield _steam_entry(name, appid, 1)

    owned = {a for a in _owned_steam_ids() if a not in installed_ids}
    if not owned or not appinfo_path().is_file():
        return
    try:
        info = read_appinfo({int(a) for a in owned})
    except (OSError, VdfError):
        return                                                 # the library of installed games still works
    for appid in sorted(info, key=int):
        name, kind = info[appid]
        if kind.lower() not in STEAM_SKIP_TYPES and not STEAM_SKIP.search(name):
            yield _steam_entry(name, appid, 0)


def shortcut_override(title: str, rules):
    """What config.json's "shortcut_overrides" says about a non-Steam shortcut: a launch command, "skip", or None.

    Each rule is {"match": "<regex>", "command": "<command line>", "exclude": "<regex>"} (all but match optional):
    a shortcut whose title matches `match` is hidden when it also matches `exclude` (e.g. a helper entry started by
    your own script), otherwise launched with `command` instead of through Steam. Invalid rules are ignored.
    """
    for rule in rules if isinstance(rules, list) else ():
        if not isinstance(rule, dict) or not isinstance(rule.get("match"), str):
            continue
        try:
            if not re.search(rule["match"], title, re.I):
                continue
            if isinstance(rule.get("exclude"), str) and rule["exclude"] and re.search(rule["exclude"], title, re.I):
                return "skip"
        except re.error:
            continue
        command = rule.get("command")
        if isinstance(command, str) and command.strip():
            return os.path.expanduser(command.strip()) if command.strip().startswith("~") else command.strip()
    return None


def scan_steam_shortcuts():
    """Yield Non-Steam games from shortcuts.vdf (real accounts only), launched through Steam.
    config.json's optional "shortcut_overrides" can hide a shortcut or give it its own launch command."""
    overrides = config.load().get("shortcut_overrides")
    for f in sorted(steam_root().glob("userdata/*/config/shortcuts.vdf")):
        if f.parent.parent.name == "0":                        # Steam's anonymous placeholder user
            continue
        try:
            tree, _ = parse_binary_vdf(f.read_bytes())
        except (OSError, VdfError):
            continue
        for entry in tree.get("shortcuts", {}).values():
            if not isinstance(entry, dict) or not isinstance(entry.get("appname"), str):
                continue
            title, aid = entry["appname"].strip(), entry.get("appid")
            if not title or not isinstance(aid, int) or SHORTCUTS_SKIP.search(title):
                continue
            gameid = (aid << 32) | 0x02000000
            cmd = f"steam steam://rungameid/{gameid}"
            override = shortcut_override(title, overrides)
            if override == "skip":
                continue
            if override:
                cmd = override
            yield Game(title, "steam", f"shortcut_{aid}", cmd, "steam", title, DEFAULT_LABEL, 1)


# ---------------------------------------------------------------- Epic (via Heroic)

_epic_cache = {"mtime": None, "items": {}}


def _epic_library():
    """{app_name: library item} from Heroic's cache. Re-read only when the file changes.

    Raises FileNotFoundError when Heroic has no library cache and ValueError when it is corrupt.
    """
    st = heroic_library().stat()
    if _epic_cache["mtime"] != st.st_mtime_ns:
        data = json.loads(_read_text(heroic_library()))
        library = data.get("library") if isinstance(data, dict) else None
        if not isinstance(library, list):
            raise ValueError("legendary_library.json has no 'library' list")
        _epic_cache["items"] = {
            i["app_name"]: i for i in library
            if isinstance(i, dict) and isinstance(i.get("app_name"), str) and isinstance(i.get("title"), str)
            and i["app_name"] and i["title"].strip()
        }
        _epic_cache["mtime"] = st.st_mtime_ns
    return _epic_cache["items"]


def scan_heroic_epic():
    """Yield Game for the Epic Games library managed by Heroic (legendary)."""
    for app_name, item in _epic_library().items():
        if EPIC_SKIP_DLC and isinstance(item.get("install"), dict) and item["install"].get("is_dlc"):
            continue
        title = item["title"].strip()
        yield Game(title, "epic", app_name, HEROIC_LAUNCH.format(app_name=app_name), "heroic",
                   title, DEFAULT_LABEL, 1 if item.get("is_installed") else 0)


def epic_art(app_name: str):
    """(portrait art_square URL, landscape art_cover URL) for an Epic game; either may be None."""
    try:
        item = _epic_library().get(app_name) or {}
    except (OSError, ValueError):
        return None, None
    return item.get("art_square") or None, item.get("art_cover") or None


def epic_install_dir(app_name: str):
    try:
        path = ((_epic_library().get(app_name) or {}).get("install") or {}).get("install_path")
    except (OSError, ValueError):
        return None
    return Path(path) if isinstance(path, str) and path else None


# ---------------------------------------------------------------- Minecraft

def scan_minecraft():
    """Yield GDLauncher Carbon instances. Raises FileNotFoundError when the launcher AppImage is gone."""
    if not GDLAUNCHER_INSTANCES.is_dir():
        return
    appimages = sorted(GDLAUNCHER_DIR.glob(GDLAUNCHER_GLOB))
    if not appimages:
        raise FileNotFoundError(f"{GDLAUNCHER_DIR}/{GDLAUNCHER_GLOB}")
    cmd = shlex.join([str(appimages[-1])])                     # newest version when several are installed
    for d in sorted(GDLAUNCHER_INSTANCES.iterdir()):
        if not d.is_dir():
            continue
        name = d.name
        try:
            name = json.loads(_read_text(d / "instance.json")).get("name") or d.name
        except (OSError, ValueError, AttributeError):
            pass
        yield Game(name, "minecraft", str(d), cmd, "minecraft", name, DEFAULT_LABEL, 1)


# ---------------------------------------------------------------- ROMs

def rom_launch_cmd(platform: str, rom: Path) -> str:
    script, scripts = EMU_FOR.get(platform), emu_scripts()
    if script and scripts and (scripts / script).is_file():
        return shlex.join([str(scripts / script), str(rom)])
    core = RETRO_CORE_FOR.get(platform)
    if core and (RETROARCH_CORES / core).is_file():
        return shlex.join(["flatpak", "run", "org.libretro.RetroArch", "-L", str(RETROARCH_CORES / core), str(rom)])
    return shlex.join(["xdg-open", str(rom)])


def _rom_files(plat_dir: Path):
    """Files under a platform folder in a stable order, without descending into media/manual folders."""
    for dirpath, dirnames, filenames in os.walk(plat_dir, onerror=lambda _e: None):
        dirnames[:] = sorted(d for d in dirnames if d.lower() not in ROM_SKIP_DIRS)
        for name in sorted(filenames):
            if os.path.splitext(name)[1].lower() in ROM_EXTS:
                path = Path(dirpath, name)
                if path.is_file():                              # drops dangling symlinks
                    yield path


def scan_roms(root: Optional[Path] = None):
    """Yield Game for every ROM under the configured / detected ROM folder.
    Raises FileNotFoundError when there is none or the drive is absent (the caller then keeps the existing rows)."""
    root = root or roms_root()
    if root is None or not root.is_dir():
        raise FileNotFoundError(root or "no ROM folder configured or found")
    for plat_dir in sorted(p for p in root.iterdir() if p.is_dir() and p.name != "emulators"):
        for f in _rom_files(plat_dir):
            base, label, display = parse_title(f.name)
            yield Game(display, plat_dir.name, str(f), rom_launch_cmd(plat_dir.name, f), "roms", base, label, 1)
