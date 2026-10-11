import support
support.fresh_db()
import os, sys, json, tempfile, stat
from pathlib import Path
from support import ok
import config, scanners, covers

REAL_CFG = config.REAL_CONFIG_PATH; cfg_before = support.fingerprint(REAL_CFG)
root = Path(tempfile.mkdtemp(prefix="wp_cfg_", dir="/tmp")); home = root / "home"; home.mkdir()
os.environ["HOME"] = str(home)
config.ROM_PATTERNS = (f"{root}/run/media/*/*/Emulation/roms", f"{root}/run/media/*/*/Games/Emulation/roms", f"{root}/media/*/*/roms", "{home}/Emulation/roms", "{home}/Games/roms", "{home}/ROMs")
mk = lambda p: (p.mkdir(parents=True, exist_ok=True), p)[1]
cfgfile = Path(os.environ["WIREPLAY_CONFIG"]); cfgfile.unlink(missing_ok=True)

# ---------------------------------------------------------------- detection
ok(config.roms_candidates() == [] and config.get_roms_dir() is None and config.get_media_dir() is None, "nothing installed -> nothing detected (None, not an invented path)")
a = mk(root / "run/media/me/Disk/Games/Emulation/roms")
for plat in ("nes", "snes", "gba", "psx"): mk(a / plat)
ok(config.roms_candidates() == [a] and config.get_roms_dir() == a, f"EmuDeck-style drive detected: {a}")
b = mk(home / "Games/roms")
ok(config.roms_candidates()[0] == a, "several candidates: the one with the most platform folders comes first")
for plat in ("nes", "snes", "gb", "gba", "n64", "psx"): mk(b / plat)
ok(config.roms_candidates()[0] == b, "…and a home folder with more platform folders wins over a thinner drive one")
ok(config.media_candidates(a) == [] and config.get_media_dir() is None, "no art folder yet -> None")
m = mk(a.parent / "tools/downloaded_media")
ok(config.media_candidates(a) == [m], f"EmuDeck art folder is found next to the ROM folder: .../{m.relative_to(a.parent.parent)}")
es = mk(home / "ES-DE/downloaded_media")
ok(m in config.media_candidates(a) and es in config.media_candidates(a), "ES-DE's folder in the home directory is a candidate too")
x = mk(root / "run/media/me/We[ird]/Emulation/roms")
ok(config.media_candidates(x) == [es], "a ROM path containing glob characters does not break art detection")

# ---------------------------------------------------------------- configured values win
config.update(roms_dir=str(root / "elsewhere/roms"), media_dir=str(root / "elsewhere/art"))
ok(config.get_roms_dir() == root / "elsewhere/roms" and config.get_media_dir() == root / "elsewhere/art", "config.json wins over detection")
ok(not config.get_roms_dir().exists(), "…even when that folder does not exist right now (an unmounted drive is reported missing, never swapped)")
config.update(roms_dir="~/Games/roms")
ok(config.get_roms_dir() == home / "Games/roms", "'~' is expanded")
config.update(roms_dir="   ", media_dir="")
ok(config.get_roms_dir() == b and config.get_media_dir() is not None, "blank values mean 'not set': detection applies")
cfgfile.write_text("[1, 2]")
ok(config.get_roms_dir() == b, "a corrupt config does not break detection")
cfgfile.write_text('{"roms_dir": 5}'); ok(config.get_roms_dir() == b, "a non-string value is ignored")

# ---------------------------------------------------------------- ensure_paths / first run
cfgfile.unlink()
ok(config.first_run_needed() and config.ensure_paths() == {} and not cfgfile.exists(), "no config.json: first run, and ensure_paths never creates the file")
cfgfile.write_text(json.dumps({"steamgriddb_api_key": "KEEP-ME"})); os.chmod(cfgfile, 0o600)
ok(not config.first_run_needed(), "an existing config.json (the owner's) is not a first run")
written = config.ensure_paths()
d = json.loads(cfgfile.read_text())
ok(written == {"roms_dir": str(b), "media_dir": str(es)} and d["roms_dir"] == str(b) and d["steamgriddb_api_key"] == "KEEP-ME", f"detected folders are written into the existing config, other keys kept: {sorted(d)}")
ok(stat.S_IMODE(cfgfile.stat().st_mode) == 0o600, "file mode preserved (0600)")
ok(config.ensure_paths() == {}, "second call: nothing to do")
config.update(roms_dir=str(a)); d0 = cfgfile.read_text()
ok(config.ensure_paths() == {} and cfgfile.read_text() == d0, "values already set are never overwritten")
config.update(first_run=True); ok(config.first_run_needed(), '"first_run": true brings the wizard back')
config.update(first_run=False); ok(not config.first_run_needed(), "…and false switches it off")

# ---------------------------------------------------------------- Steam / Heroic
ok(config.get_steam_dir() is None and config.get_heroic_dir() is None, "no Steam / Heroic in the fake home -> None")
(home / ".steam/steam/steamapps").mkdir(parents=True); mk(home / ".config/heroic")
ok(config.get_steam_dir() == home / ".steam/steam" and config.get_heroic_dir() == home / ".config/heroic", "Steam (via ~/.steam symlink location) and Heroic detected")
fl = mk(home / ".var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps").parent
ok(config.get_steam_dir() == home / ".steam/steam", "native location wins over Flatpak")
import shutil; shutil.rmtree(home / ".steam")
ok(config.get_steam_dir() == fl, "Flatpak Steam is found when it is the only one")
config.update(steam_dir=str(root / "custom/steam"), heroic_dir=str(root / "custom/heroic"))
ok(config.get_steam_dir() == root / "custom/steam" and config.get_heroic_dir() == root / "custom/heroic", "steam_dir / heroic_dir can be set in config.json")
ok(scanners.steam_root() == root / "custom/steam" and scanners.steam_apps() == root / "custom/steam/steamapps" and scanners.heroic_library() == root / "custom/heroic/store_cache/legendary_library.json", "the scanners follow the configured Steam / Heroic folders")

# ---------------------------------------------------------------- scanners + covers follow the config
roms = mk(root / "lib/roms"); (mk(roms / "snes") / "EarthBound (USA).sfc").write_bytes(b"x" * 100); (mk(roms / "emulators") / "mgba.sh").write_text("#!/bin/sh\n")
(roms / "emulators" / "mgba.sh").chmod(0o755); (mk(roms / "gba") / "Game (USA).gba").write_bytes(b"y" * 100)
config.update(roms_dir=str(roms), media_dir=str(root / "lib/art"))
games = list(scanners.scan_roms())
ok(sorted(g.platform for g in games) == ["gba", "snes"], f"scan_roms reads the configured folder: {[g.title for g in games]}")
gba = next(g for g in games if g.platform == "gba")
ok(str(roms / "emulators/mgba.sh") in gba.launch_cmd, "EmuDeck launcher scripts are found relative to the configured ROM folder")
config.update(roms_dir=str(root / "not-mounted/roms"))
try: list(scanners.scan_roms()); ok(False, "missing folder must raise")
except FileNotFoundError: ok(True, "a missing ROM folder raises FileNotFoundError (the scan then keeps the existing rows)")
config.update(roms_dir="")
for p_ in config.ROM_PATTERNS: pass
config.ROM_PATTERNS = ()
try: list(scanners.scan_roms()); ok(False, "no folder at all must raise")
except FileNotFoundError as e: ok("no ROM folder" in str(e), f"no folder configured or detected: {e}")
art = mk(root / "lib/art/snes/covers"); (art / "EarthBound (USA).png").write_bytes(b"\x89PNG" + b"z" * 50)
covers.CACHE_DIR = root / "cache"
config.update(media_dir=str(root / "lib/art"))
cov = covers.rom_cover("snes", str(roms / "snes/EarthBound (USA).sfc"))
ok(cov and cov.parent == covers.CACHE_DIR and cov.read_bytes().startswith(b"\x89PNG"), "covers use the configured art folder (and mirror it locally)")
config.update(media_dir=str(root / "nowhere"))
ok(covers.rom_cover("snes", str(roms / "snes/EarthBound (USA).sfc")) == cov, "…and serve the mirrored copy when the folder is gone")
config.update(media_dir="", roms_dir="")
ok(covers.media_root() == es and covers.rom_cover("snes", "/x/Nothing.sfc") is None, "nothing configured: ES-DE's folder in the home dir is used; a game without art -> None, no crash")
shutil.rmtree(es)
ok(covers.media_root() is None and covers.rom_cover("snes", "/x/Nothing.sfc") is None, "no art folder anywhere: media_root() is None and rom_cover returns None, no crash")

# ---------------------------------------------------------------- the key cache reloads
config.update(steamgriddb_api_key="KEY-ONE"); covers.reload_api_key(); ok(covers.api_key() == "KEY-ONE", "SteamGridDB key read")
config.update(steamgriddb_api_key="KEY-TWO"); ok(covers.api_key() == "KEY-ONE", "(cached until told otherwise)")
covers.reload_api_key(); ok(covers.api_key() == "KEY-TWO", "reload_api_key picks up a key saved in Settings")
config.update(steamgriddb_api_key=""); covers.reload_api_key(); ok(covers.api_key() is None, "a blank key means none")

# ---------------------------------------------------------------- test isolation still holds
saved, config.CONFIG_PATH = config.CONFIG_PATH, REAL_CFG
ok(config.ensure_paths() == {} and config.update(roms_dir="/x") is False, "under WIREPLAY_TEST the real config cannot be written (ensure_paths / update refuse)")
config.CONFIG_PATH = saved
ok(support.fingerprint(REAL_CFG) == cfg_before, "the real config.json was not touched by this run")
print("ALL PASS")
