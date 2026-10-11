import support
support.fresh_db()
import os, sqlite3, subprocess, sys, time, tempfile
from pathlib import Path
from support import ok
import db, engine, config, scanners

REAL_DB = Path(db.REAL_DB_PATH)
real_before = support.fingerprint(REAL_DB)
real_cfg = config.REAL_CONFIG_PATH
cfg_before = support.fingerprint(real_cfg) if real_cfg.exists() else None

# ---------------------------------------------------------------- isolation guards
ok(str(db.DB_PATH) == support.TEST_DB, f"db.DB_PATH is the test DB: {db.DB_PATH}")
try:
    db.connect(REAL_DB); ok(False, "real DB must be refused")
except RuntimeError as e:
    ok("refusing" in str(e), f"db.connect(real) refused under WIREPLAY_TEST: {e}")
try:
    with db.session(str(REAL_DB)): ok(False, "real DB via session must be refused")
except RuntimeError:
    ok(True, "db.session(real) refused too")
ok(str(config.CONFIG_PATH) == support.TEST_CFG, "config.CONFIG_PATH is the test config")
saved, config.CONFIG_PATH = config.CONFIG_PATH, config.REAL_CONFIG_PATH
ok(config.update(zzz=1) is False, "config.update(real path) refused under WIREPLAY_TEST")
config.CONFIG_PATH = saved

# ---------------------------------------------------------------- sessions table (in memory)
con = db.connect(":memory:")
G = lambda t, plat, pid, **kw: db.Game(t, plat, pid, "true", "roms", t, "Default", 1)
db.sync(con, [G("Alpha", "snes", "a"), G("Beta", "snes", "b"), G("Gamma", "epic", "g")], {"roms"})
ids = {r["title"]: r["id"] for r in con.execute("select id,title from games")}
cols = [r["name"] for r in con.execute("pragma table_info(sessions)")]
ok(cols[:5] == ["id", "game_id", "start_time", "end_time", "duration_minutes"], f"sessions table has the specified columns first: {cols}")
sid = db.start_session(con, ids["Alpha"], now=1000, pid=42, pid_start=7)
row = con.execute("select * from sessions where id=?", (sid,)).fetchone()
ok(row["start_time"] == 1000 and row["end_time"] is None and row["duration_minutes"] == 0, "start_session: row with start_time, no end_time")
db.touch_session(con, sid, now=1500, pid=43, pid_start=8)
row = con.execute("select * from sessions where id=?", (sid,)).fetchone()
ok((row["last_seen"], row["pid"], row["pid_start"]) == (1500, 43, 8), "heartbeat updates last_seen and the followed pid")
ok(db.finish_session(con, sid, 45, end_time=1000 + 2700) == 45, "finish_session returns the minutes credited")
g = db.get_game(con, ids["Alpha"])
ok(g["total_playtime_minutes"] == 45 and g["last_played"] == 3700, f"games.total_playtime_minutes=45, last_played=end ({g['total_playtime_minutes']}, {g['last_played']})")
row = con.execute("select * from sessions where id=?", (sid,)).fetchone()
ok(row["end_time"] == 3700 and row["duration_minutes"] == 45, "sessions row closed with end_time and duration_minutes")
ok(db.finish_session(con, sid, 45, end_time=9999) == 0 and db.get_game(con, ids["Alpha"])["total_playtime_minutes"] == 45,
   "second finish of the same session is a no-op (never counted twice)")
s2 = db.start_session(con, ids["Beta"], now=0)
ok(db.finish_session(con, s2, 99999, end_time=10) == db.MAX_SESSION_MINUTES and db.get_game(con, ids["Beta"])["total_playtime_minutes"] == 1440,
   "a session is capped at 24 h")
s3 = db.start_session(con, ids["Gamma"], now=50)
db.discard_session(con, s3)
r3 = con.execute("select * from sessions where id=?", (s3,)).fetchone()
ok(r3["end_time"] == 50 and r3["duration_minutes"] == 0 and db.get_game(con, ids["Gamma"])["total_playtime_minutes"] == 0 and db.get_game(con, ids["Gamma"])["last_played"] is None,
   "discard_session closes the row, credits nothing, leaves last_played alone")
ok(db.open_sessions(con) == [], "no open sessions left")
try:
    db.start_session(con, 999999); ok(False, "foreign key must reject unknown game")
except sqlite3.IntegrityError:
    ok(True, "FOREIGN KEY rejects a session for an unknown game")
s4 = db.start_session(con, ids["Alpha"], now=5000)
ok([r["id"] for r in db.open_sessions(con)] == [s4], "open_sessions lists only the unclosed one")
ok(db.finish_session(con, 424242, 5) == 0, "finish of a missing session id is a no-op")
db.finish_session(con, s4, 3, end_time=5200)
ok([r["duration_minutes"] for r in db.session_history(con, ids["Alpha"])] == [3, 45], "session_history newest first")
db.sync(con, [G("Alpha", "snes", "a")], {"roms"})
ok(db.get_game(con, ids["Alpha"])["total_playtime_minutes"] == 48, "a rescan (sync) keeps playtime")

s5 = db.start_session(con, ids["Alpha"], now=100); s6 = db.start_session(con, ids["Alpha"], now=9000)
db.finish_session(con, s6, 2, end_time=9100); db.finish_session(con, s5, 3, end_time=200)
g = db.get_game(con, ids["Alpha"])
ok(g["last_played"] == 9100 and g["total_playtime_minutes"] == 48 + 5, f"finishing an OLDER session later never moves last_played backwards (still {g['last_played']}); playtime still adds up")
# scan warnings: a launcher that was never there is silent; one that vanished (rows exist) or an unmounted drive warns
_saved_scanners = engine.SCANNERS
def _missing(): raise FileNotFoundError("/nowhere")
engine.SCANNERS = (("steam", (_missing,)), ("heroic", (_missing,)), ("roms", (_missing,)))
cw = db.connect(":memory:")
_cfg_roms = config.get_roms_dir; config.get_roms_dir = lambda: None
ok(engine.scan(cw) == [], "fresh install, nothing configured: missing Steam / Heroic / ROMs produce no warnings")
config.get_roms_dir = lambda: Path("/run/media/x/roms")
ok([w.split(":")[0] for w in engine.scan(cw)] == ["roms"], "a configured ROM folder that is missing warns (unmounted drive?)")
db.sync(cw, [db.Game("Hades", "steam", "1", "steam x", "steam", "Hades", "Default", 1)], {"steam"})
ok(sorted(w.split(":")[0] for w in engine.scan(cw)) == ["roms", "steam"], "Steam rows exist but Steam is gone: warns, and the rows are kept")
ok(cw.execute("select is_installed from games").fetchone()[0] == 1, "…rows of a missing source are never marked uninstalled")
config.get_roms_dir = _cfg_roms; engine.SCANNERS = _saved_scanners; cw.close()

# ---------------------------------------------------------------- tags (in memory)
ok(db.parse_tags(" RPG, rpg ,#Co-op,  Currently   Playing ,,") == ["RPG", "Co-op", "Currently Playing"], "parse_tags: trim, dedupe any case, drop '#', collapse spaces")
ok(db.parse_tags(["a"] * 3 + [f"t{i}" for i in range(40)])[:2] == ["a", "t0"] and len(db.parse_tags([f"t{i}" for i in range(40)])) == db.MAX_TAGS, "parse_tags caps the count")
ok(len(db.parse_tags("x" * 100)[0]) == db.MAX_TAG_LEN, "parse_tags caps the length")
db.sync(con, [db.Game("Alpha [Hack]", "snes", "a2", "true", "roms", "Alpha", "Hack", 1)], {"roms"})
saved = db.update_group_tags(con, "alpha", "snes", "RPG, Currently Playing")
tags = {r["path_or_id"]: r["tags"] for r in con.execute("select path_or_id,tags from games where platform='snes'")}
ok(saved == ["RPG", "Currently Playing"] and tags["a"] == tags["a2"] == "RPG, Currently Playing" and tags["b"] == "",
   f"tags apply to every version of the stacked group only: {tags}")
db.sync(con, [G("Alpha", "snes", "a")], {"roms"})
ok(con.execute("select tags from games where path_or_id='a'").fetchone()[0] == "RPG, Currently Playing", "a rescan keeps tags")
db.update_group_tags(con, "Beta", "snes", "rpg")
ok(db.tag_counts(con) == {"RPG": 3, "Currently Playing": 2}, f"tag_counts: {db.tag_counts(con)}")
ok(db.update_group_tags(con, "alpha", "snes", "") == [] and con.execute("select tags from games where path_or_id='a'").fetchone()[0] == "", "removing all tags clears them")
con.close()

# ---------------------------------------------------------------- migration of an old database
old = Path(tempfile.mkdtemp(prefix="wp_old_", dir="/tmp")) / "old.db"
raw = sqlite3.connect(old)
raw.executescript("""CREATE TABLE games (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, platform TEXT NOT NULL,
    path_or_id TEXT NOT NULL, launch_cmd TEXT NOT NULL, is_installed INTEGER NOT NULL DEFAULT 1, UNIQUE (platform, path_or_id));
    INSERT INTO games (title, platform, path_or_id, launch_cmd) VALUES ('Old Game', 'steam', '1', 'steam x');""")
raw.commit(); raw.close()
c2 = db.connect(old)
names = {r["name"] for r in c2.execute("pragma table_info(games)")}
ok({"tags", "notes", "custom_args", "is_favorite", "total_playtime_minutes", "last_played"} <= names, "old DB gains tags and the other columns")
ok(c2.execute("select count(*) from sessions").fetchone()[0] == 0 and c2.execute("select tags,title from games").fetchone()[:] == ("", "Old Game"), "old DB gains the sessions table; data kept")
c2.close()

# ---------------------------------------------------------------- tag query helpers
mk = lambda *t: {"tag_keys": [engine.tag_key(x) for x in t]}
known = frozenset({"rpg", "rpgmaker", "currentlyplaying"})
ok(engine.parse_query("#RPG witcher") == (["witcher"], ["rpg"]), "parse_query splits #tags from terms")
ok(engine.has_tags(mk("RPG"), ["rpg"], known) and not engine.has_tags(mk("RPGMaker"), ["rpg"], known), "an existing tag matches exactly (#rpg does not pull in rpgmaker)")
ok(engine.has_tags(mk("Currently Playing"), ["currentlyplaying"], known) and engine.has_tags(mk("Currently Playing"), ["curr"], known), "#currentlyplaying exact, #curr prefix while typing")
ok(engine.has_tags(mk("RPG", "Co-op"), ["rpg", "co-op"], known | {"co-op"}) and not engine.has_tags(mk("RPG"), ["rpg", "co-op"], known), "several #tags are ANDed")

# ---------------------------------------------------------------- Heroic matching on real processes
row = lambda **kw: {"id": 1, "platform": "epic", "path_or_id": "fakeapp0001", "is_installed": 1, "launch_cmd": "heroic heroic://launch/legendary/fakeapp0001", **kw}
orig_dir = scanners.epic_install_dir
scanners.epic_install_dir = lambda app: Path("/tmp/wp_fake_game/Foo") if app == "fakeapp0001" else None
tokens, paths = engine.heroic_match(row())
ok(tokens == [b"HEROIC_APP_NAME=fakeapp0001\0", b"LEGENDARY_APP_NAME=fakeapp0001\0"] and paths == [b"/tmp/wp_fake_game/Foo", b"\\tmp\\wp_fake_game\\Foo"], f"heroic_match tokens+paths: {tokens} {paths}")
ok(engine.heroic_match(row(is_installed=0)) is None and engine.heroic_match(row(platform="steam")) is None, "uninstalled / non-epic rows are not matched")
ok(isinstance(engine.make_session(None, row(), 0), engine.EnvSession) and engine.make_session(None, row(), 0).wait_launcher is False, "make_session picks EnvSession for Epic rows")

kids = []
def spawn(argv, **env):
    p = subprocess.Popen(argv, env={**os.environ, **env}, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)               # own process group: reap() takes helpers' children too
    kids.append(p); time.sleep(0.15); return p
def reap(*ps):
    for p in ps:
        try: os.killpg(p.pid, 9)                                   # by pid of our own child's group
        except OSError: pass
        p.wait()
try:
    a = spawn(["sleep", "60"], HEROIC_APP_NAME="fakeapp0001")
    other = spawn(["sleep", "60"], HEROIC_APP_NAME="otherapp")
    ok(set(engine.find_env_pids(tokens)) == {a.pid}, "HEROIC_APP_NAME=<app> finds exactly that game's process")
    b = spawn(["sleep", "60"], LEGENDARY_APP_NAME="fakeapp0001")
    ok(set(engine.find_env_pids(tokens)) == {a.pid, b.pid}, "LEGENDARY_APP_NAME=<app> is accepted as well")
    reap(a, b, other)
    # folder path on the command line (Linux path, and the backslash form Wine shows)
    c = spawn(["bash", "-c", "sleep 60; :", "/tmp/wp_fake_game/Foo/Game.exe"])
    d = spawn(["bash", "-c", "sleep 60; :", "Z:\\tmp\\wp_fake_game\\Foo\\Game.exe"])
    e = spawn(["bash", "-c", "sleep 60; :", "/tmp/wp_fake_game/FooBar/other"])
    found = engine.find_env_pids([], paths=paths)
    ok({c.pid, d.pid} <= set(found), "game folder in the command line is matched (Linux and Wine spelling)")
    ok(e.pid not in found, "a sibling folder sharing the name prefix (Foo vs FooBar) is not mistaken for the game")
    link = Path(tempfile.mkdtemp(prefix="wp_lnk_", dir="/tmp")) / "xdg-open"
    link.symlink_to("/usr/bin/bash")
    x = spawn([str(link), "-c", "sleep 60; :", "/tmp/wp_fake_game/Foo/"])
    ok(x.pid not in engine.find_env_pids([], paths=paths), "a file-browsing helper (xdg-open) that mentions the folder is ignored")
    reap(c, d, e, x)

    # ---------------------------------------------------------------- EnvSession lifecycle (injected clock, real processes)
    launcher = spawn(["sleep", "300"])                    # stands in for the long-lived Heroic UI
    s = engine.EnvSession(launcher, 7, tokens, 0.0, discovery=120.0, wait_launcher=False)
    ok(s.poll(10, set()) is None and s.root is None, "discovery: nothing found yet -> keep waiting")
    g1 = spawn(["sleep", "300"], HEROIC_APP_NAME="fakeapp0001")
    ok(s.poll(20, set()) is None and s.root and s.root[0] == g1.pid, "attached to the game process, not the launcher")
    g2 = spawn(["sleep", "300"], HEROIC_APP_NAME="fakeapp0001")      # a hand-over: the real exe starts
    reap(g1)
    ok(s.poll(3600, set()) is None and s.root[0] == g2.pid, "root exited, sibling alive -> hand-over, session continues")
    reap(g2)
    ok(s.poll(3602, set()) is None, "game gone 2 s ago: inside the grace period -> still open")
    minutes = s.poll(3606, set())
    ok(minutes == 60 and s.end == 3600, f"after GRACE_SECS the session ends: minutes={minutes} (an hour), end={s.end} = last moment a game process was alive")

    s = engine.EnvSession(launcher, 7, tokens, 0.0, discovery=120.0, wait_launcher=False)
    ok(s.poll(119, set()) is None, "no game seen, inside the 120 s window -> waiting")
    ok(s.poll(121, set()) == 0 and s.end == 121, "Heroic: game never seen -> 0 min after the window, does NOT wait for the Heroic UI")
    s = engine.EnvSession(launcher, 7, tokens, 0.0, discovery=120.0, wait_launcher=True)
    ok(s.poll(121, set()) is None, "Steam-style (wait_launcher) keeps waiting for the launcher")
    reap(launcher)

    # ---------------------------------------------------------------- reconcile_sessions
    con = db.connect()                                    # the test DB
    db.sync(con, [G("Run A", "snes", "ra"), G("Gone B", "snes", "rb"), G("Reuse C", "snes", "rc"), G("NoBeat D", "snes", "rd"),
                  G("Hand E", "epic", "fakeapp0001"), G("Old F", "snes", "rf")], {"roms"})
    gid = {r["title"]: r["id"] for r in con.execute("select id,title from games")}
    now = int(time.time())
    # A: still running (pid + starttime recorded) -> resumed, keeps its original start
    pa = spawn(["sleep", "300"])
    sa = db.start_session(con, gid["Run A"], now=now - 600, pid=pa.pid, pid_start=engine.proc_start(pa.pid))
    # B: gone, heartbeat 40 min after start -> credited 40 min, end_time = last heartbeat
    pb = spawn(["sleep", "300"]); pb_start = engine.proc_start(pb.pid); reap(pb)
    sb = db.start_session(con, gid["Gone B"], now=now - 7200, pid=pb.pid, pid_start=pb_start)
    db.touch_session(con, sb, now=now - 7200 + 2400)
    # C: pid alive but it is a different process (start time differs) -> pid reuse, not resumed
    pc = spawn(["sleep", "300"])
    sc = db.start_session(con, gid["Reuse C"], now=now - 3600, pid=pc.pid, pid_start=engine.proc_start(pc.pid) + 99999)
    db.touch_session(con, sc, now=now - 3600 + 900)
    # D: gone and never beat -> 0 minutes, nothing invented
    sd = db.start_session(con, gid["NoBeat D"], now=now - 5000, pid=999999, pid_start=1)
    # E: Epic: the recorded pid is dead but another process of the game carries HEROIC_APP_NAME -> re-attached
    pe = spawn(["sleep", "300"], HEROIC_APP_NAME="fakeapp0001")
    se = db.start_session(con, gid["Hand E"], now=now - 900, pid=999998, pid_start=1)
    # F: started 3 days ago, heartbeat said still running 3 days later -> capped at 24 h
    sf = db.start_session(con, gid["Old F"], now=now - 3 * 86400, pid=999997, pid_start=1)
    db.touch_session(con, sf, now=now)
    resumed, closed = engine.reconcile_sessions(con)
    res = {g["title"]: t for g, t in resumed}
    ok(set(res) == {"Run A", "Hand E"}, f"resumed: {sorted(res)}")
    ok(res["Run A"].root[0] == pa.pid and res["Run A"].start_wall == now - 600 and res["Run A"].session_id == sa, "A resumed with its original start time and session id")
    ok(res["Hand E"].root[0] == pe.pid, "E re-attached to the live process by its environment")
    cl = {gid_: m for gid_, m in closed}
    ok(cl[gid["Gone B"]] == 40, f"B credited up to the last heartbeat: {cl[gid['Gone B']]} min")
    ok(cl[gid["Reuse C"]] == 15 and engine.proc_start(pc.pid) is not None, "C: reused pid is not mistaken for the game; credited 15 min to the heartbeat")
    ok(cl[gid["NoBeat D"]] == 0, "D: no heartbeat -> 0 min")
    ok(cl[gid["Old F"]] == db.MAX_SESSION_MINUTES, "F: capped at 24 h")
    rows = {r["id"]: r for r in con.execute("select * from sessions")}
    ok(rows[sb]["end_time"] == now - 7200 + 2400 and rows[sb]["duration_minutes"] == 40, "B row: end_time = last heartbeat, duration 40")
    ok(db.get_game(con, gid["Gone B"])["total_playtime_minutes"] == 40 and db.get_game(con, gid["Gone B"])["last_played"] == now - 4800, "B game totals updated")
    ok({r["id"] for r in db.open_sessions(con)} == {sa, se}, "only the two live sessions remain open")
    ok(engine.reconcile_sessions(con) [1] == [], "running reconcile twice credits nothing twice")
    # a resumed tracker completes normally
    t = res["Run A"]
    ok(t.poll(time.monotonic(), set()) is None, "resumed session keeps running while the game does")
    reap(pa)
    m = t.poll(time.monotonic() + 100, set())             # root gone; no env tokens -> a PID-only session ends at once
    ok(m is not None and m >= 9, f"resumed PID session ends when the process exits, minutes={m} (started 10 min ago)")
    # an Epic game that vanished entirely
    reap(pe)
    m = res["Hand E"].poll(time.monotonic() + 100, set())
    ok(m is not None and m >= 14, f"resumed Epic session ends after grace, minutes={m} (started 15 min ago)")
    con.close()
finally:
    reap(*kids)
    scanners.epic_install_dir = orig_dir

real_after = support.fingerprint(REAL_DB)
ok(real_after == real_before, f"the real wireplay.db was not touched by this run {real_after}")
cfg_after = support.fingerprint(real_cfg) if real_cfg.exists() else None
ok(cfg_after == cfg_before, "the real config.json was not touched by this run")
print("ALL PASS")
