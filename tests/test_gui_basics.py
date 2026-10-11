import support
support.fresh_db(library=True)
import os, sqlite3, subprocess, sys, time, stat
from pathlib import Path
from support import ok

import db
REAL = Path(db.REAL_DB_PATH)
real_before = support.fingerprint(REAL)
with db.session() as _con:
    n_games = _con.execute("select count(*) from games").fetchone()[0]
print(f"fixture: synthetic library with {n_games} games")

import engine, covers, config
engine.SCANNERS = ()                                   # a rescan must not rewrite the fixture
covers.fetch_poster = lambda rows: None                # no network
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
import gui
app = QApplication([]); app.setStyleSheet(gui.QSS)


def pump(ms):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents(); time.sleep(0.005)


def wait_for(cond, secs, step=100):
    end = time.time() + secs
    while time.time() < end:
        pump(step)
        if cond():
            return True
    return cond()


def q(sql, *a):
    with db.session() as con:
        return con.execute(sql, a).fetchall()


class FakePresence:
    def __init__(self): self.calls = []
    def set(self, t, p, s=None): self.calls.append(("set", t, p, int(s or 0)))
    def clear(self): self.calls.append(("clear",))
    def shutdown(self, timeout=1.5): self.calls.append(("shutdown",))


w = gui.Window(); w.resize(1280, 820); w.presence = FakePresence(); w.show(); pump(500)
ok(len(q("select 1 from games")) == n_games, f"the library of {n_games} games loaded")
ok("tags" in {r["name"] for r in q("pragma table_info(games)")}, "the games table has the tags column")
ok(q("select count(*) c from sqlite_master where name='sessions'")[0]["c"] == 1, "the sessions table exists")
ok(w.chips_scroll.isVisible() and not w.tag_chips and set(w.quick_chips) == {"recent", "never"}, "no tags yet -> the chip row holds only the two quick filters")

# ---------------------------------------------------------------- tags
def group_where(pred):
    return next(g for g in w.all_groups if pred(g))


def select(group):
    w.set_tab(None); w.search.clear(); pump(50)
    i = next(n for n, g in enumerate(w.model.groups) if g["key"] == group["key"])
    w.view.setCurrentIndex(w.model.index(i)); pump(100)
    return w.model.groups[i]


w.btn_details.setChecked(True); pump(400)
stack = group_where(lambda g: len(g["rows"]) > 1 and g["platform"] != "steam")
single_a = group_where(lambda g: len(g["rows"]) == 1 and g["platform"] == "steam" and g["installed"])
single_b = group_where(lambda g: len(g["rows"]) == 1 and g["platform"] == "epic")
print("groups:", stack["base"], "|", single_a["base"], "|", single_b["base"])

select(stack); ok(w.drawer.group["key"] == stack["key"] and w.drawer.tag_input.isEnabled(), "drawer shows the stacked game; tag field enabled")
token_before = w.drawer.token
w.drawer.tag_input.setText("RPG, Currently Playing"); w.drawer.tag_input.returnPressed.emit(); pump(200)
rows = q("select tags from games where platform=? and base_title=? collate nocase", stack["platform"], stack["base"])
ok(len(rows) > 1 and all(r["tags"] == "RPG, Currently Playing" for r in rows), f"tags saved on all {len(rows)} versions of the stack")
ok(w.drawer.tags == ["RPG", "Currently Playing"] and w.drawer.tag_flow.count() == 2 and w.drawer.tag_input.text() == "", "drawer shows two chips and clears the field")
ok(w.drawer.token == token_before and w.drawer.group["key"] == stack["key"], "drawer stayed on the same game without re-rendering its poster")
ok(w.chips_scroll.isVisible() and set(w.tag_chips) == {"rpg", "currentlyplaying"}, f"tag chips appeared next to the quick filters: {sorted(w.tag_chips)}")
ok(w.tag_chips["rpg"].text() == "#RPG  1", f"chip shows the count: {w.tag_chips['rpg'].text()!r}")
ok("RPG" in w.drawer.tag_completer.model().stringList(), "completer offers existing tags")

select(single_a); w.drawer.tag_input.setText("rpg"); w.drawer.tag_input.returnPressed.emit(); pump(200)
ok(q("select tags from games where id=?", single_a["rows"][0]["id"])[0]["tags"] == "rpg", "second game tagged (case as typed)")
ok(w.tag_chips["rpg"].text().endswith("2"), f"RPG count is now 2 across spellings: {w.tag_chips['rpg'].text()!r}")
select(single_b); w.drawer.tag_input.setText("Co-op"); w.drawer.tag_input.returnPressed.emit(); pump(200)

# filtering
def shown():
    return {g["key"] for g in w.model.groups}
w.search.setText("#rpg"); pump(100)
ok(shown() == {stack["key"], single_a["key"]} and w.tag_chips["rpg"].isChecked(), "#rpg filters to the 2 tagged games; chip is checked")
w.search.setText("#RPG #currentlyplaying"); pump(100)
ok(shown() == {stack["key"]}, "#rpg #currentlyplaying ANDs the tags")
w.search.setText("#currently"); pump(100)
ok(shown() == {stack["key"]}, "#currently (prefix while typing) matches")
w.search.setText("#rpg " + single_a["base"].split()[0].lower()); pump(100)
ok(single_a["key"] in shown() and shown() <= {stack["key"], single_a["key"]}, "#tag combined with a title word")
w.search.setText("#zzz"); pump(100)
ok(shown() == set(), "unknown tag -> empty grid")
w.search.setText("#"); pump(100)
ok(len(shown()) == len(w.all_groups), "a lone '#' filters nothing")
w.search.clear(); pump(100)
w.tag_chips["rpg"].click(); pump(100)
ok(w.search.text() == "#rpg" and shown() == {stack["key"], single_a["key"]}, "clicking a collection chip filters the grid")
w.tag_chips["rpg"].click(); pump(100)
ok(w.search.text() == "" and len(shown()) == len(w.all_groups), "clicking it again clears the filter")
select(stack)
chip = w.drawer.tag_flow.itemAt(0).widget(); chip.label.click(); pump(150)
ok(w.search.text() == "#rpg" and stack["key"] in shown(), "clicking a tag badge in the drawer filters the grid")
w.search.clear(); pump(100)

# removal
select(stack)
w.drawer.tag_flow.itemAt(0).widget().x.click(); pump(200)
rows = q("select tags from games where platform=? and base_title=? collate nocase", stack["platform"], stack["base"])
ok(all(r["tags"] == "Currently Playing" for r in rows) and w.drawer.tags == ["Currently Playing"], "x removes one tag from every version")
ok(set(w.tag_chips) == {"rpg", "currentlyplaying", "co-op"} and w.tag_chips["rpg"].text().endswith("1"), f"chips refreshed: {[b.text() for b in w.tag_chips.values()]}")
w.drawer.tag_input.setText("a, A, #b ,"); w.drawer.tag_input.returnPressed.emit(); pump(150)
ok(w.drawer.tags == ["Currently Playing", "a", "b"], f"duplicates/#/empties cleaned on add: {w.drawer.tags}")
# restart: tags persist
tags_db = {r["id"]: r["tags"] for r in q("select id,tags from games where tags != ''")}
ok(len(tags_db) == len(rows) + 2, f"{len(tags_db)} rows tagged in the DB")

# ---------------------------------------------------------------- session through the real launch path (Heroic-style hand-off)
script = support.SCRATCH / "launch_epic.sh"
script.write_text("#!/usr/bin/env bash\n"
                  "# stands in for `heroic heroic://...`: forwards to the running app and exits at once\n"
                  "setsid env HEROIC_APP_NAME=wptest_app_0001 sleep 8 </dev/null >/dev/null 2>&1 &\nexit 0\n")
script.chmod(0o755)
with db.session() as con:
    con.execute("INSERT INTO games (title, base_title, platform, path_or_id, launch_cmd, is_installed) VALUES (?,?,?,?,?,1)",
                ("WP Test Epic", "WP Test Epic", "epic", "wptest_app_0001", str(script)))
    con.commit()
    gid = con.execute("select id from games where path_or_id='wptest_app_0001'").fetchone()["id"]
row = q("select * from games where id=?", gid)[0]
w.reload(); pump(100)
w.launch_row(row)
s = q("select * from sessions where game_id=?", gid)
ok(len(s) == 1 and s[0]["end_time"] is None and s[0]["start_time"] > 0, "launch creates an open sessions row at once")
ok(len(w.sessions.active) == 1 and type(w.sessions.active[0]).__name__ == "EnvSession", "an Epic launch is tracked by an EnvSession")
ok(wait_for(lambda: w.sessions.active and w.sessions.active[0].root is not None, 6), "the launcher exited at once, yet the game process (HEROIC_APP_NAME) was found")
game_pid = w.sessions.active[0].root[0]
ok(wait_for(lambda: q("select pid from sessions where game_id=?", gid)[0]["pid"] == game_pid, 4), f"followed pid {game_pid} persisted in the sessions row")
beats = []
ok(wait_for(lambda: not w.sessions.active, 16), "session ended after the game process exited (+5 s grace)")
s = q("select * from sessions where game_id=?", gid)[0]
g = q("select * from games where id=?", gid)[0]
ok(s["end_time"] is not None and s["end_time"] >= s["start_time"] + 6 and s["duration_minutes"] == 0, f"session closed: ran {s['end_time'] - s['start_time']} s -> 0 min")
ok(g["last_played"] == s["end_time"] and g["total_playtime_minutes"] == 0, "games.last_played = session end_time")
ok("Session recorded" in w.status.text(), f"status: {w.status.text()!r}")
ok(subprocess.run(["pgrep", "-f", "HEROIC_APP_NAME=wptest"], capture_output=True).returncode != 0, "no stray test process left")

# minutes accounting through the GUI handler
with db.session() as con:
    sid = db.start_session(con, gid, now=int(time.time()) - 7200)
w._session_finished(sid, gid, 95, int(time.time()))
g = q("select * from games where id=?", gid)[0]
ok(g["total_playtime_minutes"] == 95, f"_session_finished adds 95 min: {g['total_playtime_minutes']}")
ok(q("select duration_minutes from sessions where id=?", sid)[0]["duration_minutes"] == 95, "and closes the sessions row with it")
w._session_finished(0, gid, 5, int(time.time()))
ok(q("select total_playtime_minutes from games where id=?", gid)[0]["total_playtime_minutes"] == 100, "fallback without a sessions row (sid 0) still records")

# heartbeat signal path
with db.session() as con:
    sid = db.start_session(con, gid, now=int(time.time()) - 100)
w._session_beat(sid, 4242, 99, int(time.time()))
b = q("select pid,pid_start,last_seen from sessions where id=?", sid)[0]
ok((b["pid"], b["pid_start"]) == (4242, 99) and abs(b["last_seen"] - time.time()) < 5, "heartbeat handler writes pid and last_seen")
w._session_discarded(sid)
ok(q("select end_time=start_time e, duration_minutes d from sessions where id=?", sid)[0]["e"] == 1, "discard handler closes the row")

# ---------------------------------------------------------------- restart recovery
w.close(); pump(200)
child = subprocess.Popen(["sleep", "14"], stdin=subprocess.DEVNULL)
time.sleep(0.2)
import engine as E
start = int(time.time())
with db.session() as con:
    live = db.start_session(con, gid, now=start - 300, pid=child.pid, pid_start=E.proc_start(child.pid))
    other = q("select id from games where platform='snes' limit 1")[0]["id"]
    dead = db.start_session(con, other, now=start - 3000, pid=999999, pid_start=1)
    db.touch_session(con, dead, now=start - 3000 + 600)
before_total = q("select total_playtime_minutes t from games where id=?", other)[0]["t"]
w2 = gui.Window(); w2.resize(1000, 700); w2.presence = FakePresence(); w2.show(); pump(500)
ok("recovered 1 unfinished session(s): 10m" in w2.status.text() or "recovered 1 unfinished" in w2.status.text(), f"startup status: {w2.status.text()!r}")
ok("still tracking 1 running game" in w2.status.text(), "…and says one running game is tracked again")
ok(q("select total_playtime_minutes t from games where id=?", other)[0]["t"] == before_total + 10, "dead session credited up to its last heartbeat (10 min)")
ok(len(w2.sessions.active) == 1 and w2.sessions.active[0].session_id == live, "the running game is being watched again")
ok(w2.sessions.active[0].start_wall == start - 300, "with its original start time")
child.wait(timeout=20)
ok(wait_for(lambda: not w2.sessions.active, 8), "resumed session ends when the game process exits")
r = q("select * from sessions where id=?", live)[0]
ok(r["end_time"] is not None and r["duration_minutes"] >= 5, f"closed with {r['duration_minutes']} min (started 5 min before the restart)")
ok(not q("select 1 from sessions where end_time is null"), "no open sessions left in the test DB")
w2.close(); pump(200)

real_after = support.fingerprint(REAL)
ok(real_after == real_before, "the real wireplay.db is untouched")
print("ALL PASS")
