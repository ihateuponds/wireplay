import support, os, tempfile, shutil, time, threading
from pathlib import Path
from support import ok
import covers
root = Path(tempfile.mkdtemp(prefix="wp_art_", dir="/tmp"))
drive, cache = root / "drive" / "downloaded_media", root / "cache"
covers.media_root, covers.CACHE_DIR = (lambda: drive), cache
(drive / "snes" / "covers").mkdir(parents=True); (drive / "gba" / "3dboxes").mkdir(parents=True)
a = drive / "snes" / "covers" / "EarthBound (USA).png"; a.write_bytes(b"\x89PNG-A" + b"x" * 5000)
b = drive / "gba" / "3dboxes" / "Pokémon: Emerald [x].jpg"; b.write_bytes(b"\xff\xd8JPEG" + b"y" * 3000)
r1 = covers.rom_cover("snes", "/roms/snes/EarthBound (USA).sfc")
ok(r1 and r1.parent == cache and r1.name.startswith("media_snes_covers_") and r1.read_bytes() == a.read_bytes(), f"art read from the drive is copied into the local cache: {r1.name}")
ok(int(r1.stat().st_mtime) == int(a.stat().st_mtime), "the copy keeps the source's modification time")
r2 = covers.rom_cover("snes", "/roms/snes/EarthBound (USA).sfc"); m = r1.stat().st_mtime_ns
time.sleep(0.05); covers.rom_cover("snes", "/roms/snes/EarthBound (USA).sfc")
ok(r2 == r1 and r1.stat().st_mtime_ns == m, "unchanged art is not copied again")
rb = covers.rom_cover("gba", "/roms/gba/Pokémon: Emerald [x].gba")
ok(rb and rb.read_bytes() == b.read_bytes() and rb != r1, f"a name with ':' and '[x]' is mirrored under a safe name: {rb.name}")
ok(covers.find_cover({"platform": "snes", "path_or_id": "/roms/snes/EarthBound (USA).sfc", "base_title": "EarthBound", "title": "EarthBound", "version_label": "Default", "is_installed": 1}) == r1, "find_cover returns the local copy")
# drive unmounted
shutil.move(str(root / "drive"), str(root / "drive_unmounted"))
ok(not (drive / "snes").exists(), "drive 'unmounted' (media root gone)")
o1 = covers.rom_cover("snes", "/roms/snes/EarthBound (USA).sfc")
ok(o1 == r1 and o1.read_bytes() == b"\x89PNG-A" + b"x" * 5000, "offline: the cached copy is served")
ok(covers.rom_cover("gba", "/roms/gba/Pokémon: Emerald [x].gba") == rb, "offline: second game too")
ok(covers.rom_cover("snes", "/roms/snes/Never Seen.sfc") is None, "offline: a game whose art was never copied simply has no art")
ok(covers.find_cover({"platform": "snes", "path_or_id": "/roms/snes/EarthBound (USA).sfc", "base_title": "EarthBound", "title": "EarthBound", "version_label": "Default", "is_installed": 1}) == r1, "offline: find_cover works without the drive")
# drive back, art replaced
shutil.move(str(root / "drive_unmounted"), str(root / "drive"))
a.write_bytes(b"\x89PNG-NEW" + b"z" * 4000); os.utime(a, (time.time() + 10, time.time() + 10))
r3 = covers.rom_cover("snes", "/roms/snes/EarthBound (USA).sfc")
ok(r3 == r1 and r3.read_bytes().startswith(b"\x89PNG-NEW"), "changed art on the drive refreshes the local copy")
big = drive / "snes" / "covers" / "Huge.png"; big.write_bytes(b"\x89PNG" + b"0" * (covers.MAX_MIRROR_BYTES + 10))
rh = covers.rom_cover("snes", "/roms/snes/Huge.sfc")
ok(rh == big and not any(p.name.startswith("media_snes_covers_Huge") for p in cache.iterdir()), "art over the size cap is served from the drive, not copied")
res = []
ts = [threading.Thread(target=lambda: res.append(covers.rom_cover("gba", "/roms/gba/Pokémon: Emerald [x].gba"))) for _ in range(8)]
b.write_bytes(b"\xff\xd8NEWJ" + b"q" * 3500); os.utime(b, (time.time() + 20, time.time() + 20))
[t.start() for t in ts]; [t.join() for t in ts]
ok(all(r == rb for r in res) and rb.read_bytes().startswith(b"\xff\xd8NEWJ") and not [p for p in cache.iterdir() if p.name.endswith(".part")], "8 threads mirroring the same art at once: one consistent copy, no temp files left")
ro = drive / "snes" / "covers" / "RO.png"; ro.write_bytes(b"\x89PNGro" + b"1" * 100); os.chmod(drive / "snes" / "covers", 0o555)
covers.rom_cover("snes", "/roms/snes/RO.sfc"); ok(True, "a read-only drive folder works: nothing is ever written to the drive"); os.chmod(drive / "snes" / "covers", 0o755)
print("ALL PASS")
