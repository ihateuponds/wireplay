import support
support.fresh_db(library=True)
import os, sqlite3, sys, tempfile, threading, time
from pathlib import Path
from support import ok

import db
REAL = Path(db.REAL_DB_PATH); real_before = support.fingerprint(REAL)

# a fake Syncthing: a config.xml sharing a "saves" folder with another device, and one save file in it
import socket
_state = support.SCRATCH / "state"; (_state / "syncthing").mkdir(parents=True)
_saves = support.SCRATCH / "retro" / "saves"; (_saves / "mGBA").mkdir(parents=True)
(_saves / "mGBA" / "Pokémon Modern Emerald (3.5).srm").write_bytes(b"save" * 100)
(_state / "syncthing" / "config.xml").write_text(
    f'<configuration version="37"><folder id="saves" label="Saves" path="{_saves}" type="sendreceive">'
    f'<device id="AAAA"/><device id="BBBB"/></folder><device id="AAAA" name="{socket.gethostname()}"/>'
    f'<device id="BBBB" name="Handheld"/></configuration>')
os.environ["XDG_STATE_HOME"] = str(_state)
import config, covers, engine, metadata, ra_client, fakes
fake = fakes.Fake()
fakes.HLTB_GAMES += [
    {"game_id": 11, "game_name": "Pokemon Modern Emerald", "game_alias": "", "comp_main": 108000, "comp_plus": 180000, "comp_100": 360000, "release_world": 2023, "comp_all_count": 5},
    {"game_id": 12, "game_name": "EarthBound", "game_alias": "", "comp_main": 100800, "comp_plus": 115200, "comp_100": 136800, "release_world": 1994, "comp_all_count": 50},
]
metadata.BASE = fake.base
ra_client.API_BASE, ra_client.MEDIA_BASE = fake.base + "/API/", fake.base + "/"
ra_client.CACHE_DIR = Path(os.environ["WIREPLAY_CACHE"]); ra_client.BADGE_DIR = ra_client.CACHE_DIR / "ra_badges"; ra_client.DATA_DIR = ra_client.CACHE_DIR / "ra"
covers.fetch_poster = lambda rows: None
ra_client.needs_identify = lambda row: False       # this suite is about the drawer sections; the hash flow has its own tests (t_gui3, t_rahash)
engine.SCANNERS = ()
opened = []
engine.open_folder = lambda folder: opened.append(Path(folder)) or None

# which thread runs each slow call? (must never be the UI thread)
MAIN = threading.main_thread()
threads = {}
def spy(mod, name, label):
    orig = getattr(mod, name)
    def wrapper(*a, **k):
        threads.setdefault(label, []).append(threading.current_thread() is MAIN)
        return orig(*a, **k)
    setattr(mod, name, wrapper)
spy(metadata, "fetch_row", "hltb"); spy(ra_client, "refresh_game", "ra_detail"); spy(ra_client, "sync_library", "ra_sync")
spy(engine, "find_synced_save", "sync"); spy(ra_client, "fetch_badge", "badge")

# fixture rows: a ROM with no RetroAchievements set, plus a Pokemon Emerald with progress
with db.session() as con:
    db.sync(con, [db.Game("Pokémon Emerald Version", "gba", "/nowhere/gba/Pokemon Emerald Version.gba", "true", "roms", "Pokémon Emerald Version", "Default", 1)], set())
    con.commit()

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt, QTimer
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

# ---------------------------------------------------------------- 1. RetroAchievements OFF (no keys): nothing asked, nothing shown
ok(not ra_client.enabled(), "fixture: no RetroAchievements keys configured")
w = gui.Window(); w.resize(1280, 820); w.presence = FakePresence(); w.show(); pump(800)
ok(fake.count("/API/") == 0, "no keys -> not a single RetroAchievements request at startup")
ok(not w.ra_syncing, "…and no sync pending")

def group(pred):
    return next(g for g in w.all_groups if pred(g))

def pick(row_id):
    """Choose a version inside the shown stack (the drawer's version box)."""
    i = w.drawer.versions.findData(row_id)
    assert i >= 0, "version not in the drawer"
    w.drawer.versions.setCurrentIndex(i); pump(60)

SELECT_MS = []

def select(g):
    w.set_tab(None); w.search.clear(); pump(30)
    i = next(n for n, x in enumerate(w.model.groups) if x["key"] == g["key"])
    t = time.perf_counter(); w.view.setCurrentIndex(w.model.index(i)); SELECT_MS.append((time.perf_counter() - t) * 1000); pump(30)

w.btn_details.setChecked(True); pump(300)
stack = group(lambda g: g["platform"] == "gba" and len(g["rows"]) > 50)       # the 91-version "Pokémon" stack
MID = q("select id from games where title like '%Modern Emerald%' and platform='gba'")[0]["id"]
modern = stack
select(modern)
D = w.drawer
pick(MID)
ok(wait_for(lambda: D.sync_badge.isVisible(), 4), "Syncthing badge appears for a game whose save is in the Syncthing folder")
ok(D.sync_badge.text() == "🔄 Syncthing (Handheld)", f"badge text from Syncthing's config: {D.sync_badge.text()!r}")
ok(D.btn_sync.isVisible() and D.btn_sync.text() == "📂 Synced Save", "'📂 Synced Save' button shown")
D.btn_sync.click(); pump(50)
ok(len(opened) == 1 and opened[0].name == "mGBA" and (opened[0] / "Pokémon Modern Emerald (3.5).srm").is_file(), f"button opens the folder holding the .srm: {opened[0]}")
ok(wait_for(lambda: D.hltb_box.isVisible() and D.hltb_labels["main"].text(), 6), "HowLongToBeat row filled in")
ok((D.hltb_labels["main"].text(), D.hltb_labels["extra"].text(), D.hltb_labels["complete"].text()) == ("⏱️ Main: ~30h", "🗡️ Extra: ~50h", "🏆 100%: ~100h"),
   f"format: {D.hltb_labels['main'].text()} | {D.hltb_labels['extra'].text()} | {D.hltb_labels['complete'].text()}")
ok(D.year.isVisible() and D.year.text() == "2023" and D.synopsis.isVisible() and D.synopsis.text().startswith("Defy the god of death"), f"release year badge {D.year.text()!r} + synopsis under the header")
ok(not D.ra_box.isVisible(), "RetroAchievements card hidden when no keys are configured")
r = q("select hltb_main, release_year from games where title like '%Modern Emerald%' and platform='gba'")
ok(all(x["hltb_main"] == 30.0 and x["release_year"] == "2023" for x in r) and len(r) >= 1, f"…and cached in SQLite for all {len(r)} version rows")
others = q("select count(*) c from games where base_title like 'Pok%' and platform='gba' and title not like '%Modern Emerald%' and hltb_main is not NULL")[0]["c"]
ok(others == 0, "…and for none of the other 90 Pokémon versions in the same stack")
n_h = fake.count("/api/search/site") + fake.count("/game/")
select(group(lambda g: g["base"] == "EarthBound" and g["platform"] == "snes")); select(modern); pick(MID); pump(300)
ok(fake.count("/api/search/site") + fake.count("/game/") == n_h + 2 and D.hltb_labels["main"].text(), "re-opening a cached game makes no HowLongToBeat request (only EarthBound's first lookup did)")
ok(threads["hltb"] and threads["sync"] and not any(threads["hltb"]) and not any(threads["sync"]), f"HLTB + Syncthing lookups ran off the UI thread: {threads}")

# ---------------------------------------------------------------- 2. RetroAchievements ON
config.update(ra_username="tester", ra_api_key=fakes.API_KEY)
ok(ra_client.enabled(), "keys configured")
w.close(); pump(200)
w = gui.Window(); w.resize(1280, 820); w.presence = FakePresence(); w.show(); D = w.drawer
ok(wait_for(lambda: q("select count(*) c from games where ra_game_id is not null")[0]["c"] >= 3 and not w.ra_syncing, 10), "startup scan triggers a background RetroAchievements sync")
ok(threads["ra_sync"] and not any(threads["ra_sync"]), "the sync ran on a pool thread")
pump(300)
eb = group(lambda g: g["base"] == "EarthBound" and g["platform"] == "snes")
em = group(lambda g: g["platform"] == "gba" and len(g["rows"]) > 50)      # fresh: the first window's dict predates the RA sync
pe = group(lambda g: g["base"].endswith("Emerald Version") and g["platform"] == "gba")
ok(engine.ra_badge(eb) == ("🥇 Mastered", True), f"EarthBound 50/50 -> gold Mastered: {engine.ra_badge(eb)}")
ok(engine.ra_badge(pe) == ("🏆 20/63", False), f"Pokémon Emerald -> 🏆 20/63: {engine.ra_badge(pe)}")
ok(engine.ra_badge(em) == ("🏆 20/63", False), f"the 91-version Pokémon stack shows its best version (Emerald 20/63): {engine.ra_badge(em)}")
ok(q("select ra_total, ra_unlocked from games where ra_game_id=201")[0]["ra_total"] == 40, "the Modern Emerald hack set (40 achievements, 0 unlocked) was matched too")
ok(engine.ra_badge({"ra_total": 0}) is None, "no achievements -> no badge")
sw = group(lambda g: g["platform"] == "switch")
ok(engine.ra_badge(sw) is None, "Switch game has no badge (platform not covered)")

# card painting: gold box at the top-left of the EarthBound card, trophy badge on Emerald
w.search.setText("earthbound"); pump(400)
w.view.setCurrentIndex(w.model.index(0)); pump(200)
img = w.view.viewport().grab().toImage()
def has_color(img, rgb, box, tol=12):
    x0, y0, x1, y1 = box
    return any(abs(img.pixelColor(x, y).red() - rgb[0]) < tol and abs(img.pixelColor(x, y).green() - rgb[1]) < tol and abs(img.pixelColor(x, y).blue() - rgb[2]) < tol
               for x in range(x0, x1) for y in range(y0, y1))
ok(has_color(img, (0xf5, 0xc5, 0x18), (11, 11 + 30, 11 + 140, 11 + 60)), "the first EarthBound card has a gold badge under its platform badge")
img.save(support.out("ra_cards.png"))
w.search.clear(); pump(200)

# drawer: full card
w.btn_details.setChecked(True); pump(200); select(em); pick(MID); pump(200)
ok(wait_for(lambda: D.ra_box.isVisible(), 4), "RetroAchievements card shown for a matched game")
ok(wait_for(lambda: D.ra_summary.text().startswith("2/6"), 6), f"live numbers replaced the cached ones: {D.ra_summary.text()!r}")
ok("5 pts (hardcore)" in D.ra_summary.text() and D.ra_bar.maximum() == 6 and D.ra_bar.value() == 2, "progress bar 2/6 + hardcore points")
ok(not D.ra_list.isVisible() and D.ra_toggle.text().startswith("▸ Achievements (6)"), "achievement list starts collapsed")
ok(wait_for(lambda: q("select ra_total, ra_unlocked, ra_points from games where ra_game_id=201")[0]["ra_total"] == 6, 3), "live numbers stored in the DB (the cards update)")
ok(wait_for(lambda: engine.ra_badge(group(lambda g: g["platform"] == "gba" and len(g["rows"]) > 50)) == ("🏆 2/6", False), 4), "…and the stack card follows its best version: 🏆 2/6")
D.ra_toggle.click(); pump(200)
ok(D.ra_list.isVisible() and D.ra_list.count() == 6, f"expanding shows all {D.ra_list.count()} achievements")
texts = [D.ra_list.item(i).text() for i in range(6)]
ok(sum(t.startswith("✔") for t in texts) == 2 and sum(t.startswith("🔒") for t in texts) == 4, f"locked/unlocked state: {[t.split(chr(10))[0] for t in texts]}")
ok("Do thing number" in texts[0].split("\n")[1] and texts[0].split("\n")[0].endswith("(30)"), f"each row has title, points and description: {texts[0]!r}")
def icon_ok(item):
    c = item.icon().pixmap(40, 40).toImage().pixelColor(20, 20)
    return (c.red(), c.green(), c.blue()) == (0xe0, 0x31, 0x31)
ok(wait_for(lambda: all(icon_ok(D.ra_list.item(i)) for i in range(6)), 6), "badge icons downloaded and shown for every achievement")
ok(fake.count("_lock.png") >= 4 and any(p.name.endswith("_lock.png") for p in ra_client.BADGE_DIR.iterdir()), "locked achievements use the _lock badge")
ok(threads["badge"] and threads["ra_detail"] and not any(threads["badge"]) and not any(threads["ra_detail"]), "badge + detail fetches ran off the UI thread")
D.ra_toggle.click(); pump(100)
ok(not D.ra_list.isVisible(), "collapsing hides the list again")

# mastered game
select(eb); pump(200)
ok(wait_for(lambda: D.ra_award.text() == "🥇 Mastered", 4) and D.ra_bar.property("mastered") is True, "mastered game: gold award + gold progress bar")
ok(D.ra_summary.text().startswith("3/3"), f"{D.ra_summary.text()!r}")
ok(D.hltb_labels["main"].text() == "⏱️ Main: ~28h", f"EarthBound HLTB {D.hltb_labels['main'].text()!r}")
ok(not D.sync_badge.isVisible(), "no Syncthing badge for a game without a synced save")

# a game with no RA set: no card
select(sw); pump(300)
ok(not D.ra_box.isVisible(), "a Switch game has no RetroAchievements card")

# section order, top to bottom, with everything visible
select(em); pick(MID); w.resize(1280, 1500); pump(600)
body = D.title.parentWidget()
order = [("title", D.title), ("year", D.year), ("synopsis", D.synopsis), ("hltb", D.hltb_box), ("ra", D.ra_box), ("sync", D.sync_badge),
         ("playtime", D.stats), ("favorite", D.btn_fav), ("folder", D.btn_folder), ("tags", D.tag_input), ("args", D.args), ("notes", D.notes)]
ys = {n: wdg.mapTo(body, wdg.rect().topLeft()).y() for n, wdg in order}
ok(all(wdg.isVisible() for n, wdg in order), f"all sections visible for a fully-populated game: {[n for n, wdg in order if not wdg.isVisible()]}")
seq = [ys[n] for n, _ in order if n != "year"]
ok(seq == sorted(seq) and ys["year"] < ys["synopsis"], f"order: header > synopsis > HowLongToBeat > RetroAchievements > save sync & playtime > actions: {ys}")
w.grab().save(support.out("drawer_full.png"))

# ---------------------------------------------------------------- 3. the UI thread never waits for the network
fake.delay = 1.0
for t in ("hltb", "ra_detail", "sync", "badge"): threads[t] = []
with db.session() as con:
    con.execute("UPDATE games SET meta_checked=NULL, hltb_main=NULL, hltb_extra=NULL, hltb_complete=NULL, synopsis='', release_year=''"); con.commit()
D.ra_memo.clear(); D.pending_hltb.clear()
gaps, last = [], [time.perf_counter()]
def tick():
    now = time.perf_counter(); gaps.append(now - last[0]); last[0] = now
mon = QTimer(); mon.setInterval(4); mon.timeout.connect(tick); mon.start()
pump(50); gaps.clear(); last[0] = time.perf_counter()
SELECT_MS.clear()
select(group(lambda g: g["base"].endswith("Emerald Version") and g["platform"] == "gba"))
select(em); pick(MID)
select(eb)
sel_ms = max(SELECT_MS)
bar = w.view.verticalScrollBar(); w.search.clear(); pump(100)
frames = []
for i in range(60):                                  # scroll the grid while the lookups are in flight
    t = time.perf_counter(); bar.setValue(bar.value() + 120); app.processEvents(); frames.append((time.perf_counter() - t) * 1000)
pump(2500)                                            # lookups (1 s per request) now complete in the background
mon.stop()
print(f"INFO opening a drawer with 1 s network latency: slowest select = {sel_ms:.1f} ms; longest event-loop gap = {max(gaps) * 1000:.1f} ms; "
      f"scroll step avg {sum(frames) / len(frames):.1f} ms, worst {max(frames):.1f} ms")
ok(sel_ms < 100, f"opening a drawer returns at once ({sel_ms:.1f} ms) although every request takes 1 s")
ok(max(gaps) < 0.15, f"the event loop was never blocked for more than {max(gaps) * 1000:.0f} ms while lookups were in flight")
ok(sum(frames) / len(frames) < 16.0, f"grid scrolling averaged {sum(frames) / len(frames):.1f} ms per step during lookups (16.7 ms = 60 FPS)")
ok(threads["hltb"] and threads["ra_detail"] and not any(t for k in ("hltb", "ra_detail", "sync") for t in threads[k]), "every slow call ran on a worker thread")
fake.delay = 0.0

# ---------------------------------------------------------------- 4. failures are silent
with db.session() as con:
    con.execute("UPDATE games SET meta_checked=NULL, hltb_main=NULL, hltb_extra=NULL, hltb_complete=NULL, synopsis='', release_year='' WHERE title LIKE '%Emerald%'"); con.commit()
D.ra_memo.clear(); D.pending_hltb.clear(); metadata._token.update(value=None, at=0)
fake.search_status = 500; fake.ra_status = 500
select(pe); pump(1200)
ok(not D.hltb_box.isVisible() and not D.hltb_note.isVisible(), "HowLongToBeat down: the row just stays hidden (no error, no 'looking up…' stuck)")
ok(D.ra_box.isVisible() and D.ra_summary.text().startswith("2/6"), f"RetroAchievements down: the card keeps the numbers cached in the DB: {D.ra_summary.text()!r}")
ok(q("select meta_checked from games where base_title='Pokémon Emerald Version'")[0]["meta_checked"] is None, "HLTB outage is not remembered as a miss (retried on the next view)")
ok(not D.pending_hltb, "pending lookup bookkeeping cleaned up")
fake.search_status = None; fake.ra_status = None
select(eb); select(pe); pump(1500)
ok(wait_for(lambda: bool(q("select meta_checked from games where base_title='Pokémon Emerald Version'")[0]["meta_checked"]), 4), "…and succeeds (as a miss: not in fake HLTB) once the service is back")

# ---------------------------------------------------------------- 5. a play session refreshes achievements
n = fake.count("API_GetGameInfoAndUserProgress")
em_row = q("select id from games where ra_game_id=201")[0]["id"]
with db.session() as con:
    sid = db.start_session(con, em_row, now=int(time.time()) - 600)
w._session_finished(sid, em_row, 10, int(time.time()))
ok(wait_for(lambda: fake.count("API_GetGameInfoAndUserProgress") > n, 4), "finishing a session refetches that game's achievements")
ok(q("select total_playtime_minutes t from games where id=?", em_row)[0]["t"] >= 10, "…after recording the playtime")

# ---------------------------------------------------------------- 6. wrong keys: silent, no spam
n = fake.count("/API/")
config.update(ra_api_key="WRONG-KEY-0000")
ra_client._failed_credentials = None
D.ra_memo.clear()
select(em); pick(MID); pump(800)
c1 = fake.count("/API/")
select(eb); select(em); pick(MID); pump(500)
ok(fake.count("/API/") - c1 <= 1, "after one 401 the rejected key is not tried again on every drawer open")
w.close(); pump(200); fake.stop()
ok(support.fingerprint(REAL) == real_before, "the real wireplay.db is untouched")
print("ALL PASS")
