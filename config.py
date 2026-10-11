"""~/.config/wireplay/config.json: small user settings (and the SteamGridDB API key, which is never logged).

load() never raises. update() merges keys into the existing file and rewrites it atomically with the file's
existing permissions (0600 for a new file), so settings written by the GUI cannot clobber the API key.
"""
import glob
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Optional

REAL_CONFIG_PATH = Path.home() / ".config/wireplay/config.json"
CONFIG_PATH = Path(os.environ.get("WIREPLAY_CONFIG") or REAL_CONFIG_PATH)     # WIREPLAY_CONFIG: tests use their own
_lock = threading.Lock()


def load() -> dict:
    try:
        data = json.loads(CONFIG_PATH.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def update(**values) -> bool:
    """Merge `values` into the config file. Returns False (and changes nothing) if it cannot be written."""
    if os.environ.get("WIREPLAY_TEST") and CONFIG_PATH.resolve() == REAL_CONFIG_PATH.resolve():
        return False                                  # verification runs never write the real config
    with _lock:
        try:
            if CONFIG_PATH.exists():
                current = json.loads(CONFIG_PATH.read_text())
                if not isinstance(current, dict):
                    return False                      # not ours to overwrite
                mode = CONFIG_PATH.stat().st_mode & 0o777
            else:
                current, mode = {}, 0o600
            current.update(values)
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=CONFIG_PATH.parent, prefix=".config.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(current, f, indent=2)
                    f.write("\n")
                os.chmod(tmp, mode)
                os.replace(tmp, CONFIG_PATH)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            return True
        except (OSError, ValueError):                 # unreadable/corrupt file: leave it alone
            return False


# ---------------------------------------------------------------- library locations
#
# Every path below comes from config.json when set ("roms_dir", "media_dir", "steam_dir", "heroic_dir") and is
# otherwise detected. A configured path is returned as is even when it does not exist right now: an external
# drive that is unmounted must be reported as "missing", not silently replaced by something else.

# Where an EmuDeck / ES-DE style "Emulation/roms" folder usually lives. {home} is the user's home directory.
ROM_PATTERNS = (
    "/run/media/*/*/Emulation/roms", "/run/media/*/*/Games/Emulation/roms", "/run/media/*/*/roms",
    "/run/media/*/Emulation/roms",                                            # Steam Deck SD card
    "/media/*/*/Emulation/roms", "/media/*/*/Games/Emulation/roms", "/media/*/*/roms",
    "/mnt/*/Emulation/roms", "/mnt/*/Games/Emulation/roms", "/mnt/*/roms",
    "{home}/Emulation/roms", "{home}/Games/Emulation/roms", "{home}/Games/roms", "{home}/ROMs", "{home}/roms",
)
# Where box art usually is, relative to the ROM folder's parent (EmuDeck) and to the home directory (ES-DE).
MEDIA_RELATIVE = ("tools/downloaded_media", "downloaded_media", "media")
MEDIA_HOME = ("{home}/ES-DE/downloaded_media", "{home}/.emulationstation/downloaded_media")
STEAM_PATTERNS = ("{home}/.local/share/Steam", "{home}/.steam/steam", "{home}/.steam/root",
                  "{home}/.var/app/com.valvesoftware.Steam/.local/share/Steam")
HEROIC_PATTERNS = ("{home}/.config/heroic", "{home}/.var/app/com.heroicgameslauncher.hgl/config/heroic")
# Folder names that mark a directory as a ROM library (used to prefer the real one among several candidates).
PLATFORM_DIRS = frozenset("nes snes n64 gb gbc gba nds 3ds gc wii wiiu switch psx ps2 ps3 psp psvita genesis megadrive "
                          "mastersystem gamegear dreamcast saturn atari2600 pcengine arcade mame".split())


def _configured(key: str) -> Optional[Path]:
    value = load().get(key)
    return Path(value).expanduser() if isinstance(value, str) and value.strip() else None


def _expand(patterns) -> list:
    home = str(Path.home())
    found = []
    for pattern in patterns:
        for hit in sorted(glob.glob(pattern.replace("{home}", glob.escape(home)))):
            path = Path(hit)
            if path.is_dir() and path not in found:
                found.append(path)
    return found


def _rom_score(path: Path) -> int:
    try:
        return sum(1 for child in path.iterdir() if child.is_dir() and child.name.lower() in PLATFORM_DIRS)
    except OSError:
        return 0


def roms_candidates() -> list:
    """Existing ROM folders found in the usual places, best (most platform sub-folders) first."""
    found = _expand(ROM_PATTERNS)
    return sorted(found, key=lambda p: -_rom_score(p))


def media_candidates(roms: Optional[Path] = None) -> list:
    """Existing box-art folders next to the ROM folder (EmuDeck layout) or in the usual ES-DE places."""
    near = [glob.escape(str(roms.parent / rel)) for rel in MEDIA_RELATIVE] if roms else []
    return _expand([*near, *MEDIA_HOME])


def get_roms_dir() -> Optional[Path]:
    """The ROM library: config "roms_dir", else the best detected folder, else None."""
    return _configured("roms_dir") or next(iter(roms_candidates()), None)


def get_media_dir() -> Optional[Path]:
    """The box-art folder: config "media_dir", else detected next to the ROM folder, else None."""
    configured = _configured("media_dir")
    if configured:
        return configured
    return next(iter(media_candidates(get_roms_dir())), None)


def get_steam_dir() -> Optional[Path]:
    """Steam's data folder: config "steam_dir", else the first standard location (native, symlink, Flatpak) that has
    steamapps/. None when Steam is not installed."""
    configured = _configured("steam_dir")
    if configured:
        return configured
    return next((p for p in _expand(STEAM_PATTERNS) if (p / "steamapps").is_dir()), None)


def get_heroic_dir() -> Optional[Path]:
    """Heroic Games Launcher's config folder (native or Flatpak), or None."""
    configured = _configured("heroic_dir")
    if configured:
        return configured
    return next(iter(_expand(HEROIC_PATTERNS)), None)


def ensure_paths() -> dict:
    """Write the detected ROM and box-art folders into an existing config.json that has none, and return what was
    written. After the first start the setup no longer depends on detection (a changed mount point cannot swap
    the library under the user). Never creates config.json: a missing file means 'first run' (see first_run_needed).
    """
    if not CONFIG_PATH.exists():
        return {}
    cfg, new = load(), {}
    if not (isinstance(cfg.get("roms_dir"), str) and cfg["roms_dir"].strip()):
        found = next(iter(roms_candidates()), None)
        if found:
            new["roms_dir"] = str(found)
    roms_value = new.get("roms_dir") or cfg.get("roms_dir")
    roms = Path(roms_value).expanduser() if isinstance(roms_value, str) and roms_value.strip() else None
    if roms and not (isinstance(cfg.get("media_dir"), str) and cfg["media_dir"].strip()):
        media = next(iter(media_candidates(roms)), None)
        if media:
            new["media_dir"] = str(media)
    return new if new and update(**new) else {}


def first_run_needed() -> bool:
    """True for a fresh install (no config.json) or when the file says "first_run": true."""
    return not CONFIG_PATH.exists() or load().get("first_run") is True
