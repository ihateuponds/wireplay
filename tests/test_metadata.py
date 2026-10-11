import support
support.fresh_db()
import os, sys, tempfile, time, sqlite3, threading
from pathlib import Path
from support import ok
os.environ["WIREPLAY_CACHE"] = tempfile.mkdtemp(prefix="wp_cache_", dir="/tmp")     # RA/badge cache of this run only
import db, config, covers, metadata, ra_client
import fakes
from covers import NetworkError

REAL_DB = Path(db.REAL_DB_PATH); real_before = support.fingerprint(REAL_DB)
fake = fakes.Fake()
metadata.BASE = fake.base
ra_client.API_BASE = fake.base + "/API/"
ra_client.MEDIA_BASE = fake.base + "/"
ra_client.CACHE_DIR = Path(os.environ["WIREPLAY_CACHE"]); ra_client.BADGE_DIR = ra_client.CACHE_DIR / "ra_badges"; ra_client.DATA_DIR = ra_client.CACHE_DIR / "ra"
covers._backoff = lambda s: None            # keep retry tests fast

# ---------------------------------------------------------------- text helpers
N = metadata.normalize_title
ok(N("The Legend of Zelda: Ocarina of Time") == N("Legend of Zelda, The - Ocarina of Time") == "legend of zelda ocarina of time", "normalize_title: 'The', punctuation, ', The'")
ok(N("Pokémon Emerald") == "pokemon emerald" and N("Mario & Luigi") == "mario and luigi" and N("Hades II") != N("Hades"), "normalize_title: accents, &, sequels stay distinct")
ok(metadata.hours(84937) == 23.6 and metadata.hours(0) is None and metadata.hours(None) is None and metadata.hours("x") is None, "hours(): seconds -> 0.1 h, 0/None -> None")
ok([metadata.fmt_hours(h) for h in (23.6, 4.5, 4.0, 0.5, 120.0, None, 0)] == ["~24h", "~4.5h", "~4h", "~0.5h", "~120h", "", ""], f"fmt_hours: {[metadata.fmt_hours(h) for h in (23.6, 4.5, 4.0, 0.5, 120.0, None, 0)]}")
syn = metadata.short_synopsis(fakes.SUMMARY)
ok(syn == 'Defy the god of death as you hack and slash your way out of the Underworld. Fight through "Hades" & beyond in this rogue-like.', f"synopsis: 2 sentences, HTML + entities cleaned: {syn!r}")
ok(metadata.short_synopsis("Dr. Mario is a puzzle game. Drop pills! Clear viruses.") == "Dr. Mario is a puzzle game. Drop pills!", "synopsis: 'Dr.' is not a sentence end")
long = metadata.short_synopsis("word " * 200 + "end.")
ok(len(long) <= metadata.SYNOPSIS_CHARS + 1 and long.endswith("…"), f"synopsis: capped at {metadata.SYNOPSIS_CHARS} chars with an ellipsis ({len(long)})")
ok(metadata.short_synopsis(None) == "" and metadata.short_synopsis("   ") == "", "synopsis: empty in, empty out")

# ---------------------------------------------------------------- matching
R = fakes.HLTB_GAMES
ok(metadata.best_match("Hades", R)["game_id"] == 62941, "exact title beats the sequel that also contains the word")
ok(metadata.best_match("Hades II", R)["game_id"] == 118218, "…and the sequel is found by its own name")
ok(metadata.best_match("Pokemon Emerald", R)["game_id"] == 5, "a game is found through its HLTB alias")
ok(metadata.best_match("Portal", R) is None and metadata.best_match("Hade", R) is None, "'Portal' != 'Portal 2'; a partial name is not trusted")
ok(metadata.best_match("", R) is None and metadata.best_match("Hades", [None, "x", {}]) is None, "empty / junk input -> None")

# ---------------------------------------------------------------- HowLongToBeat client against the fake
t0 = len(fake.log)
meta = metadata.fetch("Hades")
ok(meta and (meta.main, meta.extra, meta.complete, meta.year) == (23.6, 48.6, 95.3, "2020"), f"fetch: times in hours + year: {meta.main, meta.extra, meta.complete, meta.year}")
ok(meta.synopsis.startswith("Defy the god of death"), "fetch: synopsis comes from the game page")
reqs = fake.log[t0:]
ok(all(h.get("referer") == metadata.BASE + "/" for m, p, h in reqs), "Referer (the site's own address) sent on every request")
ok(all(h["user-agent"].startswith("wireplay/") and "Mozilla" not in h["user-agent"] for m, p, h in reqs), f"honest User-Agent, not a browser: {reqs[0][2]['user-agent']!r}")
n_init = fake.count("/init")
metadata.fetch("Portal 2")
ok(fake.count("/init") == n_init, "the search token is reused for the next search")
fake.search_403_once = True
meta2 = metadata.fetch("Portal 2")
ok(meta2 and meta2.main == 8.1 and fake.count("/init") == n_init + 1, "an expired token (403) is refreshed once and the search retried")
ok(metadata.fetch("Totally Unknown Title Zzz") is None, "no confident match -> None (not an error)")
nt = metadata.fetch("No Times Game")
ok(nt is not None and not nt.has_data and nt.main is None, "a match with no times and no summary has_data == False")
fake.page_status = 500
mp = metadata.fetch("Hades")
ok(mp and mp.main == 23.6 and mp.synopsis == "", "game page down: the times are still returned, synopsis empty")
fake.page_status = None
fake.search_status = 500
try: metadata.fetch("Hades"); ok(False, "500 must raise")
except NetworkError as e: ok(True, f"search outage -> NetworkError: {e}")
fake.search_status = 429
try: metadata.fetch("Hades"); ok(False, "429 must raise")
except NetworkError as e: ok(True, f"rate limit -> NetworkError: {e}")
fake.search_status = None
metadata._token.update(value=None, at=0)

# ---------------------------------------------------------------- permanent cache (fetch_row)
con = db.connect()
G = lambda title, plat, pid, src="roms": db.Game(title, plat, pid, "true", src, title, "Default", 1)
db.sync(con, [G("Hades", "steam", "1145360", "steam"), G("Hades", "switch", "/roms/switch/hades.nsp"), G("Hades [Hack]", "switch", "/roms/switch/hades2.nsp"),
              G("Nobody Knows This", "snes", "/roms/snes/nk.sfc"), G("Portal 2", "steam", "620", "steam")], {"steam", "roms"})
con.execute("UPDATE games SET base_title='Hades' WHERE path_or_id='/roms/switch/hades2.nsp'"); con.commit()      # a second version of the same group
row = lambda pid: con.execute("select * from games where path_or_id=?", (pid,)).fetchone()
ok(db.metadata_missing(row("1145360")), "a fresh row needs a lookup")
t0 = len(fake.log)
m = metadata.fetch_row(row("1145360")["id"])
r1 = row("1145360")
ok(m and (r1["hltb_main"], r1["hltb_extra"], r1["hltb_complete"], r1["release_year"]) == (23.6, 48.6, 95.3, "2020") and r1["synopsis"].startswith("Defy"), "fetch_row stores times, year, synopsis in SQLite")
ok(r1["meta_checked"] and not db.metadata_missing(r1), "…and the row is no longer 'missing' (cached permanently)")
sw = [row("/roms/switch/hades.nsp"), row("/roms/switch/hades2.nsp")]
ok(all(r["hltb_main"] is None for r in sw), "the Switch rows are untouched by a Steam lookup")
n = len(fake.log)
m2 = metadata.fetch_row(row("/roms/switch/hades.nsp")["id"])
ok(len(fake.log) == n and m2.main == 23.6, "another platform's lookup is served from the cached twin with NO network request")
sw = [row("/roms/switch/hades.nsp"), row("/roms/switch/hades2.nsp")]
ok(all(r["hltb_main"] == 23.6 and r["synopsis"].startswith("Defy") for r in sw), "both versions of the stacked group were updated")
t = len(fake.log)
ok(metadata.fetch_row(row("/roms/snes/nk.sfc")["id"]) is None and row("/roms/snes/nk.sfc")["meta_checked"], "a miss is remembered (meta_checked set, no data)")
ok(not db.metadata_missing(row("/roms/snes/nk.sfc")), "…and not retried right away")
con.execute("UPDATE games SET meta_checked=? WHERE path_or_id='/roms/snes/nk.sfc'", (int(time.time()) - 40 * 86400,)); con.commit()
ok(db.metadata_missing(row("/roms/snes/nk.sfc")), "…but retried after 30 days")
con.execute("UPDATE games SET meta_checked=? WHERE path_or_id='1145360'", (int(time.time()) - 400 * 86400,)); con.commit()
ok(not db.metadata_missing(row("1145360")), "a hit is never refetched, however old")
fake.search_status = 500
before = row("620")["meta_checked"]
try: metadata.fetch_row(row("620")["id"]); ok(False, "outage must raise")
except NetworkError: ok(row("620")["meta_checked"] == before and before is None, "a network failure stores nothing (will be retried on the next view)")
fake.search_status = None
metadata._token.update(value=None, at=0)
db.update_game_metadata(con, row("620")["id"], 1.5, None, 3.0, "Hello.", 1999)
r = row("620")
ok((r["hltb_main"], r["hltb_extra"], r["hltb_complete"], r["synopsis"], r["release_year"]) == (1.5, None, 3.0, "Hello.", "1999"), "update_game_metadata(con, id, main, extra, comp, synopsis, year) as specified")
db.sync(con, [G("Hades", "steam", "1145360", "steam")], {"steam"})
ok(row("1145360")["hltb_main"] == 23.6, "a rescan keeps the cached metadata")
cols = {c["name"]: c["type"] for c in con.execute("pragma table_info(games)")}
ok(all(cols.get(c) == t for c, t in (("hltb_main", "REAL"), ("hltb_extra", "REAL"), ("hltb_complete", "REAL"), ("synopsis", "TEXT"), ("release_year", "TEXT"))), "schema has the five specified columns")

# a collection stack: many different games share one base title (91 Pokémon versions in the real library)
db.sync(con, [db.Game("Pokemon - Emerald Version", "gba", "/roms/gba/stack_em.gba", "true", "roms", "Pokémon", "Emerald Version", 1),
              db.Game("Pokemon - Sapphire Version", "gba", "/roms/gba/stack_sa.gba", "true", "roms", "Pokémon", "Sapphire Version", 1)], {"roms"})
metadata.fetch_row(row("/roms/gba/stack_em.gba")["id"])
ok(row("/roms/gba/stack_em.gba")["hltb_main"] == 37.5 and row("/roms/gba/stack_em.gba")["release_year"] == "2004", "a stack member is looked up by its OWN title ('Pokemon - Emerald Version' found on HLTB)")
ok(row("/roms/gba/stack_sa.gba")["hltb_main"] is None and row("/roms/gba/stack_sa.gba")["meta_checked"] is None, "…and the other 'Pokémon' versions in the stack are untouched")

# ---------------------------------------------------------------- RetroAchievements: off by default
ok(ra_client.credentials() is None and not ra_client.enabled(), "no keys in the config -> RetroAchievements is off")
n = len(fake.log)
ok(ra_client.sync_library() == 0 and len(fake.log) == n, "…and sync_library() does nothing (zero requests)")
config.update(ra_username="  ", ra_api_key="")
ok(ra_client.credentials() is None, "blank username / key count as not configured")
config.update(ra_username="tester", ra_api_key=fakes.API_KEY)
ok(ra_client.credentials() == ("tester", fakes.API_KEY) and ra_client.enabled(), "username + key enable it")

# ---------------------------------------------------------------- consoles, matching
ids = ra_client.console_ids()
ok(ids["snes"] == 3 and ids["nes"] == 7 and ids["n64"] == 2 and ids["psx"] == 12 and ids["gba"] == 5, f"console ids verified by name: gba={ids['gba']}")
ok("switch" not in ids and "ps3" not in ids, "platforms RetroAchievements does not cover are absent")
(ra_client.DATA_DIR / "consoles.json").unlink()
fake.gba_wrong_id = True
ids = ra_client.console_ids()
ok(ids["gba"] == 99, f"an id whose name does not match is corrected from the live list: gba -> {ids['gba']}")
fake.gba_wrong_id = False
(ra_client.DATA_DIR / "consoles.json").unlink()
MK = ra_client.match_key
ok(MK("~Hack~ EarthBound: Halloween") == MK("EarthBound - Halloween") == "earthbound halloween", "match_key strips ~Hack~ and punctuation")
ok(MK("Pokémon Emerald Version") == MK("Pokemon - Emerald") == "pokemon emerald" and MK("Version") == "version", "match_key drops a trailing Version/Edition (but never empties a title)")
rows = [{"id": 1, "base_title": "EarthBound", "title": "EarthBound"}, {"id": 2, "base_title": "EarthBound - Halloween", "title": "EarthBound - Halloween"},
        {"id": 3, "base_title": "The Legend of Zelda - A Link to the Past", "title": "The Legend of Zelda - A Link to the Past"},
        {"id": 4, "base_title": "Unlisted Game", "title": "Unlisted Game"}]
games = [{"id": 100, "title": "EarthBound", "n": 50}, {"id": 101, "title": "~Hack~ EarthBound: Halloween", "n": 12},
         {"id": 102, "title": "Legend of Zelda, The: A Link to the Past", "n": 70}]
ok(ra_client.match_games(rows, games) == [(1, 100, 50), (2, 101, 12), (3, 102, 70)], "match_games: exact, hack set, article/comma forms")
stack_rows = [{"id": 10, "title": "Pokemon - Emerald Version", "base_title": "Pokémon"}, {"id": 11, "title": "Pokemon - Sapphire Version", "base_title": "Pokémon"},
              {"id": 12, "title": "Pokémon Emerald Version [Hack]", "base_title": "Pokémon"}]
ok(ra_client.match_games(stack_rows, [{"id": 200, "title": "Pokémon Emerald Version", "n": 63}]) == [(10, 200, 63)],
   "stack members are matched by their own titles, not the shared base title (Sapphire has no set; a [Hack] row does not inherit Emerald's)")
tr = [{"id": 20, "title": "EarthBound", "base_title": "EarthBound"}, {"id": 21, "title": "EarthBound [De, v1.2.2]", "base_title": "EarthBound"},
      {"id": 22, "title": "EarthBound [Hack]", "base_title": "EarthBound"}, {"id": 23, "title": "EarthBound [Rev 1]", "base_title": "EarthBound"},
      {"id": 24, "title": "EarthBound [Subset - Low Level Run]", "base_title": "EarthBound"}]
tg = [{"id": 100, "title": "EarthBound", "n": 50}, {"id": 105, "title": "EarthBound [Subset - Low Level Run]", "n": 20}]
ok(ra_client.match_games(tr, tg) == [(20, 100, 50), (23, 100, 50), (24, 105, 20)],
   "translations/hacks do NOT inherit the original's set; a plain [Rev 1] does; a subset finds its own set")
ok(ra_client.match_games(tr + [{"id": 25, "title": "EarthBound - Halloween [Hack]", "base_title": "EarthBound - Halloween"}],
                         tg + [{"id": 106, "title": "~Hack~ EarthBound: Halloween", "n": 12}])[-1] == (25, 106, 12),
   "a [Hack] ROM matches RetroAchievements' own ~Hack~ set for it")
ok(ra_client.match_games(rows[:1], [{"id": 5, "title": "~Hack~ EarthBound", "n": 1}, {"id": 100, "title": "EarthBound", "n": 50}]) == [(1, 100, 50)], "a real game wins over a ~Hack~ set with the same name")

# ---------------------------------------------------------------- library sync end-to-end
db.sync(con, [G("EarthBound", "snes", "/roms/snes/eb.sfc"), G("EarthBound - Halloween [Hack]", "snes", "/roms/snes/ebh.sfc"),
              G("Pokémon Emerald Version", "gba", "/roms/gba/em.gba"), G("Pokemon - Modern Emerald", "gba", "/roms/gba/me.gba"),
              db.Game("Pokemon - Emerald Version", "gba", "/roms/gba/em2.gba", "true", "roms", "Pokémon", "Emerald Version", 1),
              G("Unlisted Game", "snes", "/roms/snes/u.sfc"), G("Switch Thing", "switch", "/roms/switch/s.nsp")], {"roms"})
con.execute("UPDATE games SET base_title='EarthBound - Halloween' WHERE path_or_id='/roms/snes/ebh.sfc'"); con.commit()
ok(ra_client.sync_due(), "first run: a sync is due")
t0 = len(fake.log)
n = ra_client.sync_library()
rr = lambda pid: con.execute("select ra_game_id, ra_total, ra_unlocked, ra_mastered, ra_points from games where path_or_id=?", (pid,)).fetchone()
ok(n == 6, f"sync_library matched 6 rows (incl. the two stack rows): {n}")
ok(tuple(rr("/roms/snes/eb.sfc")) == (100, 50, 50, 1, 0), f"EarthBound: 50/50, mastered: {tuple(rr('/roms/snes/eb.sfc'))}")
ok(tuple(rr("/roms/gba/em.gba")) == (200, 63, 20, 0, 0), f"Pokémon Emerald (paged progress, 2 per page): 20/63: {tuple(rr('/roms/gba/em.gba'))}")
ok(tuple(rr("/roms/snes/ebh.sfc"))[:3] == (101, 12, 0), "a hack set is matched and has 0 unlocked (no progress yet)")
ok(tuple(rr("/roms/gba/me.gba"))[:3] == (201, 40, 0), "~Hack~ Pokemon Modern Emerald matched")
ok(tuple(rr("/roms/gba/em2.gba"))[:3] == (200, 63, 20), "a stack member ('Pokemon - Emerald Version' under base 'Pokémon') is matched and has the same progress")
ok(rr("/roms/snes/u.sfc")[0] is None and rr("/roms/switch/s.nsp")[0] is None, "unlisted / unsupported platforms have no match")
ok(fake.count("API_GetUserCompletionProgress") >= 2, f"user progress was fetched in pages: {fake.count('API_GetUserCompletionProgress')} calls for 3 results")
bogus = con.execute("select id from games where path_or_id='/roms/snes/u.sfc'").fetchone()[0]
con.execute("UPDATE games SET ra_game_id=555, ra_total=9, ra_unlocked=3 WHERE id=?", (bogus,)); con.commit()
ra_client.sync_library()
ok(tuple(rr("/roms/snes/u.sfc")) == (None, 0, 0, 0, 0), "a match that no longer holds is cleared on the next sync (no stale badge)")
ok(not ra_client.sync_due(), "after a sync none is due for hours")
c_before = fake.count("API_GetGameList")
con.execute("UPDATE games SET ra_unlocked=0"); con.commit()
ra_client.sync_library()
ok(fake.count("API_GetGameList") == c_before and rr("/roms/gba/em.gba")["ra_unlocked"] == 20, "game lists are cached (no refetch); progress is refreshed")
ok(all("y=" + fakes.API_KEY in p for m, p, h in fake.log[t0:] if "/API/" in p), "every API call carries y=<key>")

# ---------------------------------------------------------------- one game
d = ra_client.game_detail(200)
ok(d["total"] == 6 and d["unlocked"] == 2 and d["points"] == 5 and d["points_total"] == 5 * (1 + 2 + 3 + 4 + 5 + 6), f"detail: totals/hardcore points: {d['total'], d['unlocked'], d['points']}")
ok(d["achievements"][0]["id"] == 2006 and [a["unlocked"] for a in d["achievements"]].count(True) == 2, "achievements ordered by DisplayOrder; unlock flags")
ok(d["achievements"][-1]["hardcore"] and d["achievements"][-1]["badge"] == "20001", "hardcore flag + badge name")
ok(not d["mastered"], "2/6 is not mastered")
ok(ra_client.game_detail(4040)["achievements"] == [] and ra_client.game_detail(31337) is None, "an empty set / unknown game id are handled")
ra_client.refresh_game(200)
r = rr("/roms/gba/em.gba")
ok((r["ra_total"], r["ra_unlocked"], r["ra_points"]) == (6, 2, 5), f"refresh_game stores total, unlocked and hardcore points: {tuple(r)}")

# ---------------------------------------------------------------- badges
n = len(fake.log)
p = ra_client.fetch_badge("20001")
ok(p and p.is_file() and p.parent == ra_client.BADGE_DIR and p.name == "20001.png", f"badge cached in the badge dir: {p.name}")
ok(fake.count("/Badge/20001.png") == 1, "downloaded once")
ra_client.fetch_badge("20001")
ok(fake.count("/Badge/20001.png") == 1 and ra_client.cached_badge("20001") == p, "second call served from the cache")
pl = ra_client.fetch_badge("20001", locked=True)
ok(pl and pl.name == "20001_lock.png", "locked variant is a separate file")
n = len(fake.log)
ok(all(ra_client.fetch_badge(x) is None for x in ("../etc/passwd", "a/b", "", "x" * 80, "bad name")) and len(fake.log) == n, "invalid badge names are refused with no request (no path traversal)")
ok(ra_client.fetch_badge("bad001") is None and not (ra_client.BADGE_DIR / "bad001.png").exists(), "a non-PNG answer is not cached")
ok(ra_client.fetch_badge("gone01") is None, "404 -> None")

# ---------------------------------------------------------------- errors never leak the key; rejected credentials stop the traffic
def leak_check(label, fn):
    try:
        fn()
    except NetworkError as e:
        text = f"{e!s} {e!r} {getattr(e, 'status', '')}"
        ok(fakes.API_KEY not in text, f"{label}: error text has no API key ({str(e)[:50]!r})")
        return e
    except Exception as e:
        ok(False, f"{label}: unexpected {type(e).__name__}: {e}")
    ok(False, f"{label}: expected NetworkError")
fake.ra_status = 500
leak_check("HTTP 500", lambda: ra_client.game_detail(100))
fake.ra_status = None
fake.ra_error_json = "Invalid API Key! key=" + fakes.API_KEY
leak_check("JSON error echoing the key", lambda: ra_client.game_detail(100))
ok(ra_client.credentials() is None, "credentials the server rejected are not used again")
fake.ra_error_json = None
n = len(fake.log)
ok(ra_client.sync_library() == 0 and len(fake.log) == n, "…so no further requests are made")
config.update(ra_username="tester", ra_api_key=fakes.API_KEY + "")      # same values: still rejected
ok(ra_client.credentials() is None, "re-saving identical values does not retry")
config.update(ra_api_key=fakes.API_KEY + " ")                           # whitespace stripped -> identical
ok(ra_client.credentials() is None, "(whitespace-only change does not count as a new key)")
ra_client._failed_credentials = None
config.update(ra_api_key="WRONG-KEY-123")
e = leak_check("401 wrong key", lambda: ra_client.game_detail(100))
ok(e.status == 401 and ra_client.credentials() is None, "401 -> NetworkError(401) and the wrong key is parked")
config.update(ra_api_key=fakes.API_KEY)
ok(ra_client.credentials() == ("tester", fakes.API_KEY) and ra_client.game_detail(100)["total"] == 3, "a corrected key works again at once")
old_base = ra_client.API_BASE
ra_client.API_BASE = "http://127.0.0.1:9/API/"
leak_check("connection refused", lambda: ra_client.game_detail(100))
ra_client.API_BASE = old_base

# the HTTP user-agent for RA is the generic one; metadata does not touch real network under test
try:
    covers.http_get("https://example.org/")
    ok(False, "real network must be blocked in tests")
except NetworkError:
    ok(True, "WIREPLAY_TEST blocks non-local network access")
con.close(); fake.stop()
ok(support.fingerprint(REAL_DB) == real_before, "the real wireplay.db was not touched")
print("ALL PASS")
