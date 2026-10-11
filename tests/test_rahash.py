import support
support.fresh_db()
import os, sys, hashlib, tempfile, time, zipfile
from pathlib import Path
from support import ok
os.environ["WIREPLAY_CACHE"] = tempfile.mkdtemp(prefix="wp_cache_", dir="/tmp")
import db, config, covers, metadata, ra_client, fakes
from covers import NetworkError
REAL = Path(db.REAL_DB_PATH); real_before = support.fingerprint(REAL)
fake = fakes.Fake()
ra_client.API_BASE, ra_client.MEDIA_BASE = fake.base + "/API/", fake.base + "/"
ra_client.CACHE_DIR = Path(os.environ["WIREPLAY_CACHE"]); ra_client.BADGE_DIR = ra_client.CACHE_DIR / "ra_badges"; ra_client.DATA_DIR = ra_client.CACHE_DIR / "ra"
covers._backoff = lambda s: None
M = lambda b: hashlib.md5(b).hexdigest()

# ---------------------------------------------------------------- hash rules per console
rom = bytes((i * 7 + 3) % 256 for i in range(40960))
nes = b"NES\x1a" + b"\x02\x01\x01\x00" + b"\x00" * 8 + rom
ok(ra_client.hash_bytes("nes", nes) == M(rom), "NES: the 16-byte iNES header is not hashed")
ok(ra_client.hash_bytes("nes", rom) == M(rom), "NES: a headerless ROM hashes as is")
snes = rom[:32768 * 1]
ok(ra_client.hash_bytes("snes", b"\xaa" * 512 + snes) == M(snes) and len(snes) % 1024 == 0, "SNES: a 512-byte copier header is dropped (size % 1024 == 512)")
ok(ra_client.hash_bytes("snes", snes) == M(snes), "SNES: a headerless dump hashes as is")
for plat in ("gb", "gbc", "gba", "genesis", "megadrive"):
    ok(ra_client.hash_bytes(plat, rom) == M(rom), f"{plat}: whole file")
z64 = b"\x80\x37\x12\x40" + bytes((i * 13) % 251 for i in range(4096 - 4))
v64 = bytearray(z64); v64[0::2], v64[1::2] = z64[1::2], z64[0::2]
n64 = bytearray(z64); n64[0::4], n64[1::4], n64[2::4], n64[3::4] = z64[3::4], z64[2::4], z64[1::4], z64[0::4]
ok(bytes(v64[:4]) == b"\x37\x80\x40\x12" and bytes(n64[:4]) == b"\x40\x12\x37\x80", "fixture: .v64 / .n64 byte orders look right")
ok(ra_client.hash_bytes("n64", z64) == ra_client.hash_bytes("n64", bytes(v64)) == ra_client.hash_bytes("n64", bytes(n64)) == M(z64), "N64: .z64, .v64 and .n64 of one game give the same hash (big-endian)")
ok(ra_client.hash_bytes("gc", rom) is None and ra_client.hash_bytes("psx", rom) is None and ra_client.hash_bytes("nes", b"") is None, "disc platforms / empty data -> None (title matching only)")
tmp = Path(tempfile.mkdtemp(prefix="wp_roms_", dir="/tmp"))
(tmp / "g.nes").write_bytes(nes)
with zipfile.ZipFile(tmp / "one.zip", "w") as z: z.writestr("inner.nes", nes)
with zipfile.ZipFile(tmp / "two.zip", "w") as z: z.writestr("a.nes", nes); z.writestr("b.nes", nes)
(tmp / "bad.zip").write_bytes(b"not a zip")
ok(ra_client.rom_hash(tmp / "g.nes", "nes") == M(rom), "rom_hash reads a file")
ok(ra_client.rom_hash(tmp / "one.zip", "nes") == M(rom), "a .zip with one ROM is hashed by its content")
ok(ra_client.rom_hash(tmp / "two.zip", "nes") is None and ra_client.rom_hash(tmp / "bad.zip", "nes") is None and ra_client.rom_hash(tmp / "missing.nes", "nes") is None, "ambiguous / corrupt / missing -> None")
old_cap = ra_client.MAX_HASH_BYTES; ra_client.MAX_HASH_BYTES = 1000
ok(ra_client.rom_hash(tmp / "g.nes", "nes") is None, "files over the size cap are not read"); ra_client.MAX_HASH_BYTES = old_cap

# ---------------------------------------------------------------- identify_row
snes_a = snes + b"A" * 1024                                   # three SNES "dumps"
(tmp / "eb.sfc").write_bytes(b"\x00" * 512 + snes_a)          # official EarthBound, with a copier header
(tmp / "ebmod.sfc").write_bytes(snes_a + b"patched")          # a modified ROM: title says EarthBound, hash is unknown to RA
(tmp / "weird.sfc").write_bytes(snes_a)                       # same dump as the official one, but a name that title matching misses
fakes.HASHES = {100: [M(snes_a)], 200: [M(rom)], 102: ["0" * 32]}
config.update(ra_username="tester", ra_api_key=fakes.API_KEY)
con = db.connect()
G = lambda t, plat, path: db.Game(t, plat, str(path), "true", "roms", t, "Default", 1)
db.sync(con, [G("EarthBound", "snes", tmp / "eb.sfc"), G("EarthBound [Patched]", "snes", tmp / "ebmod.sfc"), G("Totally Different Name", "snes", tmp / "weird.sfc"),
              G("Mario Thing", "snes", tmp / "gone.sfc"), G("Metroid", "nes", tmp / "g.nes"), G("Luigi", "gc", tmp / "x.gcm")], {"roms"})
rid = lambda path: con.execute("select id from games where path_or_id=?", (str(path),)).fetchone()[0]
st = lambda path: tuple(con.execute("select ra_game_id, ra_total, ra_verified, rom_hash is not null from games where path_or_id=?", (str(path),)).fetchone())
ra_client.sync_library()
ok(st(tmp / "eb.sfc")[0] == 100 and st(tmp / "ebmod.sfc")[0] is None and st(tmp / "weird.sfc")[0] is None, "before hashing: title matching found EarthBound only (the patched ROM was not matched; the odd name missed)")
con.execute("UPDATE games SET ra_game_id=100, ra_total=50, ra_unlocked=12, ra_checked=1 WHERE path_or_id=?", (str(tmp / "ebmod.sfc"),)); con.commit()   # a (wrong) earlier title guess
n = len(fake.log)
r = ra_client.identify_row(rid(tmp / "eb.sfc"))
ok(r and r["verified"] == 1 and r["ra_game_id"] == 100 and r["hash"] == M(snes_a), "a title-matched ROM is confirmed by its hash (copier header stripped)")
ok(st(tmp / "eb.sfc")[0] == 100 and st(tmp / "eb.sfc")[2:] == (1, 1) and st(tmp / "eb.sfc")[1] > 0, f"…verified=1 and the hash cached in rom_hash: {st(tmp / 'eb.sfc')}")
r = ra_client.identify_row(rid(tmp / "weird.sfc"))
ok(r and r["changed"] and r["ra_game_id"] == 100 and (st(tmp / "weird.sfc")[0], st(tmp / "weird.sfc")[2]) == (100, 1), "title matching MISSED, the hash found the set: exact match 100")
r = ra_client.identify_row(rid(tmp / "ebmod.sfc"))
ok(r and r["verified"] == -1 and r["ra_game_id"] is None, "a wrong title guess is overruled: the hash says RA has no set for this ROM")
ok((st(tmp / "ebmod.sfc")[0], st(tmp / "ebmod.sfc")[1], st(tmp / "ebmod.sfc")[2]) == (None, 0, -1) and con.execute("select ra_unlocked, ra_mastered from games where id=?", (rid(tmp / "ebmod.sfc"),)).fetchone()[:] == (0, 0), "…its title-based badge and counts are cleared")
ok(ra_client.identify_row(rid(tmp / "gone.sfc")) is None and st(tmp / "gone.sfc")[2] == 0, "ROM on an unmounted drive: nothing happens, nothing is recorded")
ok(ra_client.identify_row(rid(tmp / "x.gcm")) is None, "a platform we cannot hash (GameCube): skipped")
m = fake.count("API_GetGameList")
r1 = ra_client.identify_row(rid(tmp / "eb.sfc")); ok(r1 is None, "an already verified ROM is not looked at again")
orig = ra_client.rom_hash; ra_client.rom_hash = lambda *a: (_ for _ in ()).throw(AssertionError("must not re-read the ROM"))
ra_client.identify_row(rid(tmp / "ebmod.sfc")); ra_client.rom_hash = orig
ok(fake.count("API_GetGameList") == m, "a 'no set' verdict is cached too (no re-read, no new request)")
con.execute("UPDATE games SET ra_hash_checked=? WHERE path_or_id=?", (int(time.time()) - 31 * 86400, str(tmp / "ebmod.sfc"))); con.commit()
fakes.HASHES[100].append(M(snes_a + b"patched"))                # RetroAchievements adds the hash later
ra_client.DATA_DIR.joinpath("gamesh_3.json").unlink()           # (and our weekly cache expires)
r = ra_client.identify_row(rid(tmp / "ebmod.sfc"))
ok(r and r["verified"] == 1 and st(tmp / "ebmod.sfc")[0] == 100, "after a month a 'no set' verdict is asked again, and now matches")

# a game list cached with hashes; hash_index
idx = ra_client.hash_index(3)
ok(idx[M(snes_a)] == (100, 50) and "gamesh_3" in " ".join(p.name for p in ra_client.DATA_DIR.iterdir()), "hash_index comes from the cached GetGameList?h=1 (one request per console per week)")
ok(all("h=1" in p for m_, p, h in fake.log if "API_GetGameList" in p), "game lists are requested with hashes (documented endpoint; GetGameID is not in the web API)")

# network failure: the hash is still stored
db.sync(con, [G("Metroid 2", "nes", tmp / "g.nes")], {"roms"}); (tmp / "g2.nes").write_bytes(nes)
con.execute("UPDATE games SET path_or_id=? WHERE title='Metroid 2'", (str(tmp / "g2.nes"),)); con.commit()
(ra_client.DATA_DIR / "gamesh_7.json").unlink(missing_ok=True)       # the NES hash list is not cached right now
fake.ra_status = 500
try: ra_client.identify_row(rid(tmp / "g2.nes")); ok(False, "outage must raise")
except NetworkError: ok(st(tmp / "g2.nes") == (None, 0, 0, 1), "RetroAchievements unreachable: NetworkError, verdict undecided, but the hash IS cached")
fake.ra_status = None
r = ra_client.identify_row(rid(tmp / "g2.nes"))
ok(r and r["verified"] == -1, "…and the next attempt needs no ROM read, only the hash list")

# sync respects verdicts
ra_client.sync_library()
ok(st(tmp / "ebmod.sfc")[0] == 100 and st(tmp / "ebmod.sfc")[2] == 1 and st(tmp / "weird.sfc")[0] == 100 and st(tmp / "weird.sfc")[2] == 1, "a library sync never undoes a hash verdict")
con.execute("UPDATE games SET ra_game_id=NULL, ra_total=0, ra_verified=-1, ra_hash_checked=? WHERE path_or_id=?", (int(time.time()), str(tmp / "eb.sfc"))); con.commit()
ra_client.sync_library()
ok(st(tmp / "eb.sfc")[0] is None and st(tmp / "eb.sfc")[2] == -1, "…including 'RA has no set': title matching does not re-add it")
ok(con.execute("select ra_checked from games where path_or_id=?", (str(tmp / "weird.sfc"),)).fetchone()[0] is not None, "hash-matched rows are included in the progress update")
# credentials off
config.update(ra_api_key="")
n = len(fake.log); ok(ra_client.identify_row(rid(tmp / "weird.sfc")) is None and len(fake.log) == n and not ra_client.needs_identify(con.execute("select * from games limit 1").fetchone()), "without keys nothing is hashed or requested")
con.close(); fake.stop()
ok(support.fingerprint(REAL) == real_before, "the real wireplay.db was not touched")
print("ALL PASS")
