import support
support.fresh_db(library=True)
import os, sqlite3, subprocess, sys, tempfile, threading, time, hashlib
from pathlib import Path
from support import ok

import db
REAL = Path(db.REAL_DB_PATH); real_before = support.fingerprint(REAL)

import config, covers, engine, metadata, ra_client, fakes
fake = fakes.Fake()
metadata.BASE = fake.base
ra_client.API_BASE, ra_client.MEDIA_BASE = fake.base + "/API/", fake.base + "/"
ra_client.CACHE_DIR = Path(os.environ["WIREPLAY_CACHE"]); ra_client.BADGE_DIR = ra_client.CACHE_DIR / "ra_badges"; ra_client.DATA_DIR = ra_client.CACHE_DIR / "ra"
covers.fetch_poster = lambda rows: None
engine.SCANNERS = ()
launched = []

from PySide6.QtWidgets import QApplication, QMenu
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QContextMenuEvent
import gui
app = QApplication([]); app.setStyleSheet(gui.QSS)

def pump(ms):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents(); time.sleep(0.003)

def wait_for(cond, secs, step=50):
    end = time.time() + secs
    while time.time() < end:
        pump(step)
        if cond(): return True
    return cond()

def q(sql, *a):
    with db.session() as con:
        return con.execute(sql, a).fetchall()

class FakePresence:
    def set(self, *a): pass
    def clear(self): pass
    def shutdown(self, timeout=1.5): pass

def new_window():
    w = gui.Window(); w.resize(1280, 900); w.presence = FakePresence(); w.launch_row = lambda row: launched.append(row["id"]); w.show(); pump(500)
    return w

now = int(time.time()); DAY = 86400

# ---------------------------------------------------------------- fixtures in the test DB
with db.session() as con:
    # six single-version installed games: "recent" ones and an old one
    singles = [r["id"] for r in con.execute("select min(id) id from games where is_installed=1 and platform='steam' group by base_title having count(*)=1 limit 12")]
    A, B, C, D_ = singles[0], singles[1], singles[2], singles[3]
    for gid, days_ago, minutes in ((A, 0, 42), (B, 45, 90), (C, 1, 75), (D_, 3, 20)):
        sid = db.start_session(con, gid, now=now - days_ago * DAY - minutes * 60)
        db.finish_session(con, sid, minutes, end_time=now - days_ago * DAY)
    # EarthBound stack: two versions played, the second more recently
    eb = [r["id"] for r in con.execute("select id from games where platform='snes' and base_title='EarthBound' order by id")]
    EB_OLD, EB_NEW = eb[0], eb[1]
    s1 = db.start_session(con, EB_OLD, now=now - 20 * DAY - 600); db.finish_session(con, s1, 10, end_time=now - 20 * DAY)
    s2 = db.start_session(con, EB_NEW, now=now - 2 * DAY - 1800); db.finish_session(con, s2, 30, end_time=now - 2 * DAY)
    con.execute("UPDATE games SET notes='Use the sound-stone patch', custom_args='--fullscreen' WHERE id=?", (EB_OLD,))
    con.commit()
    names = {r["id"]: r["title"] for r in con.execute("select id, title from games where id in (?,?,?,?)", (A, B, C, D_))}
print("fixtures: A,B,C,D =", list(names.values()), "| EarthBound old/new:", EB_OLD, EB_NEW)

w = new_window(); D = w.drawer
def group(pred): return next(g for g in w.all_groups if pred(g))
def gid_group(gid): return next(g for g in w.all_groups if any(r["id"] == gid for r in g["rows"]))
def select(g):
    w.set_tab(None); w.search.clear(); w.quick = None; w.refilter(); pump(30)
    i = next(n for n, x in enumerate(w.model.groups) if x["key"] == g["key"])
    w.view.setCurrentIndex(w.model.index(i)); pump(60)
w.btn_details.setChecked(True); pump(300)

# ---------------------------------------------------------------- 5. session history
select(gid_group(A))
ok(D.stats.text().startswith("Playtime: 42m") and D.stats.text().split("\n")[0].endswith("▸") and D.stats.text().split("\n")[1].startswith("Last played:") and D.stats.isEnabled(), f"playtime line shows an arrow when sessions exist: {D.stats.text()!r}")
ok(not D.session_list.isVisible(), "the session list starts collapsed")
D.stats.click(); pump(100)
ok(D.session_list.isVisible() and D.session_list.text() == "Today · 42m", f"clicking the playtime expands the sessions: {D.session_list.text()!r}")
ok(D.stats.text().split("\n")[0].endswith("▾"), "the arrow flips")
select(gid_group(C)); D.stats.click(); pump(80)
ok(D.session_list.text() == "Yesterday · 1h 15m", f"yesterday: {D.session_list.text()!r}")
select(gid_group(D_)); D.stats.click(); pump(80)
ok(D.session_list.text() == "3 days ago · 20m", f"older: {D.session_list.text()!r}")
select(gid_group(B)); D.stats.click(); pump(80)
ok(D.session_list.text().endswith(" · 1h 30m") and D.session_list.text()[:4] == "2026", f"over a month: a date: {D.session_list.text()!r}")
D.stats.click(); pump(50)
ok(not D.session_list.isVisible(), "clicking again collapses")
nosess = next(g for g in w.all_groups if g["installed"] and not g["last_played"] and g["platform"] == "steam")
select(nosess)
ok(not D.stats.isEnabled() and "▸" not in D.stats.text() and D.stats.toolTip() == "No sessions recorded yet", "a game with no sessions: plain playtime line, nothing to expand")
select(gid_group(A)); D.stats.click(); pump(50); select(gid_group(C)); pump(50)
ok(not D.stats.isChecked() and not D.session_list.isVisible(), "switching to another game collapses the list")
# several sessions in order, newest first, capped
with db.session() as con:
    for i in range(12):
        sid = db.start_session(con, A, now=now - (i + 1) * 3600 * 5); db.finish_session(con, sid, 5 + i, end_time=now - (i + 1) * 3600 * 5 + 60 * (5 + i))
select(gid_group(A)); D.stats.click(); pump(80)
lines = D.session_list.text().split("\n")
ok(len(lines) == gui.Drawer.SESSIONS_SHOWN == 8 and lines[0] == "Today · 42m", f"latest {len(lines)} sessions, newest first: {lines[:3]} …")
D.stats.click(); pump(50)

# ---------------------------------------------------------------- 4. quick filters
w.reload(); pump(300)
recent = {g["key"] for g in w.all_groups if engine.played_recently(g["last_played"])}
never = {g["key"] for g in w.all_groups if g["installed"] and not g["playtime"] and not g["last_played"]}
ok(len(recent) >= 4 and {gid_group(x)["key"] for x in (A, C, D_)} <= recent and gid_group(B)["key"] not in recent, f"fixture: {len(recent)} recently played groups (B, 45 days ago, is not one)")
ok(w.quick_chips["recent"].text() == f"🕒 Played Recently  {len(recent)}" and w.quick_chips["never"].text() == f"📦 Never Played  {len(never)}", f"chips show counts: {w.quick_chips['recent'].text()} / {w.quick_chips['never'].text()}")
ok(w.chips_scroll.isVisible(), "the chip row is always visible (quick filters live there)")
w.quick_chips["recent"].click(); pump(150)
shown = {g["key"] for g in w.model.groups}
ok(shown == recent and w.quick_chips["recent"].isChecked(), f"🕒 Played Recently shows exactly the {len(recent)} recent games")
w.quick_chips["never"].click(); pump(150)
shown = {g["key"] for g in w.model.groups}
ok(shown == never and w.quick_chips["never"].isChecked() and not w.quick_chips["recent"].isChecked(), f"📦 Never Played switches over (exclusive) and shows {len(never)} backlog games")
ok(gid_group(A)["key"] not in shown and gid_group(B)["key"] not in shown and all(g["installed"] for g in w.model.groups), "…none played, none uninstalled")
w.set_tab("steam"); pump(100)
ok(all(g["platform"] == "steam" for g in w.model.groups) and {g["key"] for g in w.model.groups} == {k for k in never if next(x for x in w.all_groups if x["key"] == k)["platform"] == "steam"}, "quick filters combine with the platform tab")
w.search.setText("#nothing"); pump(100)
ok(len(w.model.groups) == 0, "…and with a #tag / search (nothing matches here)")
w.search.clear(); w.quick_chips["never"].click(); pump(100)
ok(w.quick is None and not w.quick_chips["never"].isChecked(), "clicking an active chip clears it")
w.set_tab(None); pump(100)
ok(len(w.model.groups) == len(w.all_groups), "no filter -> all games again")

# ---------------------------------------------------------------- 4. the shelf
ok(w.shelf_box.isVisible() and w.shelf_view.isVisible(), "shelf is shown once something has been played")
order = [g["orig"]["last_played"] for g in w.shelf_model.groups]
ok(order == sorted(order, reverse=True) and len(order) <= engine.SHELF_MAX, f"newest first, at most {engine.SHELF_MAX}: {len(order)} games")
ok(w.shelf_model.groups[0]["orig"]["key"] == gid_group(A)["key"], "the game played most recently (today) is first")
ok(all(g["orig"]["installed"] for g in w.shelf_model.groups) and gid_group(B)["key"] in [g["orig"]["key"] for g in w.shelf_model.groups], "only installed games; the 45-day-old one is still on the shelf")
ok(all(g["key"][0] == "shelf" for g in w.shelf_model.groups), "shelf cards have their own poster-cache keys (no clash with the grid)")
ok(w.shelf_view.height() == w.shelf_delegate.card_h + 14 and w.shelf_delegate.poster_w == gui.SHELF_POSTER_W, f"compact cards ({gui.SHELF_POSTER_W}px posters, strip {w.shelf_view.height()}px high)")
launched.clear()
w.shelf_view.doubleClicked.emit(w.shelf_model.index(0)); pump(50)
ok(launched == [A], f"double-click resumes the game: {launched}")
eb_g = gid_group(EB_NEW)
sh = [g for g in w.shelf_model.groups if g["orig"]["key"] == eb_g["key"]]
ok(len(sh) == 1, "the EarthBound stack (27 versions) is on the shelf once")
launched.clear(); w.shelf_view.activated.emit(w.shelf_model.index(w.shelf_model.groups.index(sh[0]))); pump(50)
ok(launched == [EB_NEW], f"for a stack the version played last is resumed (not the first one): {launched}")
w.btn_details.setChecked(True); w.shelf_view.clicked.emit(w.shelf_model.index(0)); pump(100)
ok(D.group and D.group["key"] == gid_group(A)["key"], "a single click on a shelf card shows it in the details drawer")
# collapse / persistence
w.shelf_toggle.click(); pump(100)
ok(not w.shelf_view.isVisible() and w.shelf_box.isVisible() and w.shelf_toggle.text().startswith("▸"), "collapsing hides the cards but keeps the header")
ok(config.load().get("shelf_collapsed") is True, "collapsed state saved in the (test) config")
w.close(); pump(200)
w = new_window(); D = w.drawer
ok(not w.shelf_view.isVisible() and w.shelf_toggle.text().startswith("▸"), "restarted: still collapsed")
w.shelf_toggle.click(); pump(100)
ok(w.shelf_view.isVisible() and config.load().get("shelf_collapsed") is False, "expanded again and remembered")
img = w.shelf_view.viewport().grab().toImage(); img.save(support.out("shelf.png"))
ok(not img.isNull() and img.width() > 100, "the shelf paints")
w.btn_details.setChecked(True); pump(200)

# ---------------------------------------------------------------- 3. collection browser
eb_g = group(lambda g: g["platform"] == "snes" and g["base"] == "EarthBound")
n_ver = len(eb_g["rows"])
ok(n_ver > gui.COLLECTION_MIN, f"fixture: EarthBound has {n_ver} versions")
select(eb_g); pump(200)
ok(D.btn_collection.isVisible() and D.btn_collection.text() == f"🎮 Browse Collection ({n_ver})", f"drawer shows the prominent button: {D.btn_collection.text()!r}")
two = group(lambda g: len(g["rows"]) == 2 and g["installed"])
select(two); pump(100)
ok(not D.btn_collection.isVisible() and D.versions.isVisible(), "a 2-version game keeps its combo box and has no collection button")
select(eb_g)
dialogs = []
w._run_dialog = lambda dlg: (dialogs.append(dlg), False)[1]
D.btn_collection.click(); pump(100)
ok(len(dialogs) == 1 and isinstance(dialogs[0], gui.CollectionDialog), "the button opens the collection browser")
dlg = dialogs[0]
ok(len(dlg.items) == n_ver and dlg.model.rowCount() == n_ver and dlg.count.text() == f"showing {n_ver} of {n_ver}", f"all {n_ver} versions listed")
old = next(it for it in dlg.items if it["id"] == EB_OLD); new = next(it for it in dlg.items if it["id"] == EB_NEW)
ok(old["playtime"] == 10 and new["playtime"] == 30, "per-version playtime comes from fresh DB rows")
kinds = {k: len([it for it in dlg.items if k in it["chips"]]) for k in dlg.chips}
ok({"translation", "revision"} <= set(dlg.chips) and all(dlg.chips[k].text() == f"#{k}  {n}" for k, n in kinds.items()), f"kind chips with counts: {dict(sorted(kinds.items(), key=lambda kv: -kv[1])[:5])}")
dlg.search.setText("new controls"); pump(50)
hits = [it["label"] + it["file"] for it in dlg.model.items]
ok(0 < dlg.model.rowCount() < n_ver and all("new controls" in h.lower() for h in hits), f"search filters versions by name/file: {dlg.model.rowCount()} match 'new controls'")
dlg.search.clear(); pump(50)
dlg.chips["translation"].click(); pump(50)
tr = {it["id"] for it in dlg.model.items}
ok(tr == {it["id"] for it in dlg.items if "translation" in it["chips"]} and 0 < len(tr) < n_ver, f"#translation chip: {len(tr)} versions")
dlg.chips["revision"].click(); pump(50)
both = {it["id"] for it in dlg.model.items}
ok(both == {it["id"] for it in dlg.items if {"translation", "revision"} & set(it["chips"])} and len(both) >= len(tr), f"two chips show versions with either: {len(both)}")
dlg.chips["translation"].click(); dlg.chips["revision"].click(); pump(50)
ok(dlg.model.rowCount() == n_ver, "chips off -> everything again")
dlg.sort.setCurrentIndex(1); pump(50)
ok(dlg.model.items[0]["id"] == EB_NEW and dlg.model.items[1]["id"] == EB_OLD, "sort by playtime puts the most played first")
dlg.sort.setCurrentIndex(2); pump(50)
ok(dlg.model.items[0]["id"] == EB_NEW, "sort by last played puts the latest first")
dlg.sort.setCurrentIndex(0)
i_old = next(i for i, it in enumerate(dlg.model.items) if it["id"] == EB_OLD)
dlg.view.setCurrentIndex(dlg.model.index(i_old)); pump(50)
ok("Use the sound-stone patch" in dlg.info_notes.toPlainText() and "--fullscreen" in dlg.info_args.text() and not dlg.info_args.isHidden(), "a single click shows that version's notes and launch arguments")
ok("Playtime: 10m" in dlg.info_meta.text() and "Last played: 20 days ago" in dlg.info_meta.text(), f"…and its playtime/last played: {dlg.info_meta.text()[:100]}")
got = []
dlg.launch_requested.connect(lambda r: got.append(r["id"]))
dlg.view.doubleClicked.emit(dlg.model.index(i_old)); pump(50)
ok(got == [EB_OLD] and dlg.result() == 1, "double-click launches that version and closes the browser")
# show-in-details through the window glue
w._run_dialog = lambda dlg_: (dialogs.append(dlg_), False)[1]
w.open_collection(eb_g); pump(50); d3 = dialogs[-1]
d3.show_requested.emit(EB_NEW); pump(200)
w._show_version(eb_g, EB_NEW); pump(300)
ok(D.row_id == EB_NEW and D.group["key"] == eb_g["key"], f"'Show in details' selects the stack and that version in the drawer (row {D.row_id})")
# routing: big stacks open the browser from a click/Enter; small stacks keep the pick-list
dialogs.clear(); w.last_launch = 0
w.activate(eb_g); ok(len(dialogs) == 1 and isinstance(dialogs[0], gui.CollectionDialog), "activating a big stack opens the collection browser (not a 27-row pick-list)")
dialogs.clear(); w.last_launch = 0
w.activate(two); ok(len(dialogs) == 1 and isinstance(dialogs[0], gui.VersionDialog), "a 2-version game still gets the simple version dialog")
# context menu
class FakeMenu:
    seen = []
    def __init__(self, parent=None): self.acts = []
    def addAction(self, text): a = type("Act", (), {"text": text})(); self.acts.append(a); return a
    def exec(self, pos=None): FakeMenu.seen = [a.text for a in self.acts]; return next(a for a in self.acts if "Browse All Versions" in a.text)
gui.QMenu = FakeMenu
w.set_tab(None); w.search.setText("earthbound"); pump(300)
rect = w.view.visualRect(w.model.index(0)); pos = rect.center()
heard = []; w.view.browse_collection.connect(lambda g: heard.append(g["base"]))
w.view.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Mouse, pos, w.view.viewport().mapToGlobal(pos)))
ok(heard == ["EarthBound"] and not any("Select Version" in t for t in FakeMenu.seen), "right-click menu offers 'Browse All Versions…' for a big stack and it works")
w.search.clear(); pump(100)
dlg.grab().save(support.out("collection.png"))

# ---------------------------------------------------------------- 1. RA hash matching through the UI
config.update(ra_username="tester", ra_api_key=fakes.API_KEY)
rom = bytes((i * 11 + 5) % 256 for i in range(32768))
tmp = Path(tempfile.mkdtemp(prefix="wp_hashrom_", dir="/tmp"))
(tmp / "hashgame.sfc").write_bytes(b"\x00" * 512 + rom)
(tmp / "badrom.sfc").write_bytes(rom + b"modified")
fakes.HASHES = {100: [hashlib.md5(rom).hexdigest()]}
with db.session() as con:
    db.sync(con, [db.Game("EarthBound Hash Test", "snes", str(tmp / "hashgame.sfc"), "true", "roms", "EarthBound Hash Test", "Default", 1),
                  db.Game("Odd Name For The Same ROM", "snes", str(tmp / "badrom.sfc"), "true", "roms", "Odd Name For The Same ROM", "Default", 1)], set())
    con.commit()
    HID = con.execute("select id from games where path_or_id=?", (str(tmp / "hashgame.sfc"),)).fetchone()[0]
    BID = con.execute("select id from games where path_or_id=?", (str(tmp / "badrom.sfc"),)).fetchone()[0]
    con.execute("UPDATE games SET ra_game_id=100, ra_total=50, ra_unlocked=5 WHERE id=?", (BID,)); con.commit()    # a wrong earlier title guess
w.close(); pump(200)
spy_threads = []
orig_identify = ra_client.identify_row
ra_client.identify_row = lambda gid: (spy_threads.append(threading.current_thread() is threading.main_thread()), orig_identify(gid))[1]
w = new_window(); D = w.drawer; w.btn_details.setChecked(True); pump(300)
wait_for(lambda: not w.ra_syncing, 8)
select(gid_group(HID)); pump(100)
ok(wait_for(lambda: D.ra_verified.isVisible() and D.ra_box.isVisible(), 6), "opening a ROM in the drawer hashes it: the RetroAchievements card appears with '✓ matched by its hash'")
ok(q("select ra_game_id, ra_verified, rom_hash is not null from games where id=?", HID)[0][:] == (100, 1, 1), "…title matching had missed this name; the hash found set 100 and cached rom_hash")
ok(spy_threads and not any(spy_threads), "the hashing ran on a worker thread")
ok(engine.ra_badge(gid_group(HID)) is not None, f"…and the card shows a trophy badge: {engine.ra_badge(gid_group(HID))}")
select(gid_group(BID)); pump(100)
ok(wait_for(lambda: q("select ra_verified from games where id=?", BID)[0][0] == -1, 6), "a ROM whose hash RA does not know is verified as 'no set'")
ok(wait_for(lambda: not D.ra_box.isVisible(), 3) and q("select ra_game_id, ra_total from games where id=?", BID)[0][:] == (None, 0), "…its wrong title-based trophy badge is gone from the drawer and the DB")
ok(wait_for(lambda: engine.ra_badge(gid_group(BID)) is None, 3), "…and from the card")
# launch hook
with db.session() as con:
    con.execute("UPDATE games SET ra_verified=0, rom_hash=NULL, ra_game_id=NULL, ra_total=0, ra_unlocked=0 WHERE id=?", (HID,)); con.commit()
w.close(); pump(200)
w = new_window(); D = w.drawer
w.launch_row = gui.Window.launch_row.__get__(w)                  # the real launch_row, with a harmless fake process
engine.launch = lambda row: (subprocess.Popen(["sleep", "0.2"]), f"Launched {row['title']}")
w.launch_row(next(r for g in w.all_groups for r in g["rows"] if r["id"] == HID))
ok(wait_for(lambda: q("select ra_verified, ra_game_id from games where id=?", HID)[0][:] == (1, 100), 6), "launching a ROM hashes it too (the badge becomes exact)")
w.close(); pump(300)
ra_client.identify_row = orig_identify
ok(support.fingerprint(REAL) == real_before, "the real wireplay.db is untouched")
fake.stop()
print("ALL PASS")
