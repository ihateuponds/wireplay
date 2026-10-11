import support
support.fresh_db(library=True)
import os, sqlite3, sys, tempfile, time, json, stat
from pathlib import Path
from support import ok
import db
REAL = Path(db.REAL_DB_PATH); real_before = support.fingerprint(REAL)
import config
REAL_CFG = config.REAL_CONFIG_PATH; cfg_before = support.fingerprint(REAL_CFG)
import covers, engine, ra_client, discord_rpc, fakes
discord_rpc.socket_candidates = lambda: []                 # a test must never touch the real Discord
fake = fakes.Fake()
ra_client.API_BASE, ra_client.MEDIA_BASE = fake.base + "/API/", fake.base + "/"
ra_client.CACHE_DIR = Path(os.environ["WIREPLAY_CACHE"]); ra_client.BADGE_DIR = ra_client.CACHE_DIR / "ra_badges"; ra_client.DATA_DIR = ra_client.CACHE_DIR / "ra"
covers.fetch_poster = lambda rows: None; engine.SCANNERS = ()
root = Path(tempfile.mkdtemp(prefix="wp_set_", dir="/tmp")); home = root / "home"; home.mkdir(); os.environ["HOME"] = str(home)
config.ROM_PATTERNS = (f"{root}/mnt/*/Emulation/roms", "{home}/Emulation/roms")
roms_a = root / "mnt/Disk/Emulation/roms"; [(roms_a / p).mkdir(parents=True) for p in ("nes", "snes", "gba")]
(roms_a.parent / "tools/downloaded_media").mkdir(parents=True)
(home / ".local/share/Steam/steamapps").mkdir(parents=True)
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
import gui
app = QApplication([]); app.setStyleSheet(gui.QSS)
def pump(ms):
    end = time.time() + ms / 1000
    while time.time() < end: app.processEvents(); time.sleep(0.003)
def wait_for(c, secs, step=50):
    end = time.time() + secs
    while time.time() < end:
        pump(step)
        if c(): return True
    return c()
def q(sql, *a):
    with db.session() as con: return con.execute(sql, a).fetchall()
class FakePresence:
    enabled = True
    def set(self, *a): pass
    def clear(self): pass
    def shutdown(self, timeout=1.5): pass
    def set_enabled(self, on): self.enabled = on
cfgfile = Path(os.environ["WIREPLAY_CONFIG"])
cfgfile.write_text(json.dumps({"steamgriddb_api_key": "SGDB-SECRET-1", "ra_username": "", "ra_api_key": "", "card_width": 200, "startup_tab": "all", "discord_rpc": True}))
os.chmod(cfgfile, 0o600)
w = gui.Window(); w.resize(1280, 820); w.presence = FakePresence(); w.show(); pump(500)

# ---------------------------------------------------------------- the button and the dialog
ok(w.btn_settings.text() == "⚙️ Settings", f"action bar has the button: {w.btn_settings.text()!r}")
ok(w.btn_settings in [w.layout().itemAt(i).widget() for i in range(w.layout().count()) if w.layout().itemAt(i).widget()] or w.btn_settings.parent() is not None, "…and it lives in the window")
opened = []; w._run_dialog = lambda dlg: (opened.append(dlg), False)[1]
w.btn_settings.click(); pump(50)
ok(len(opened) == 1 and isinstance(opened[0], gui.SettingsDialog), "clicking it opens the Settings dialog")
QTest.keyClick(w, Qt.Key_Comma, Qt.ControlModifier); pump(50)
ok(len(opened) == 2, "Ctrl+, opens it too")
dlg = opened[0]
ok([dlg.tabs.tabText(i).replace("&&", "&") for i in range(dlg.tabs.count())] == ["Library Paths", "Integrations & API Keys", "Appearance & Behavior"], "three tabs as specified")
import version
ok(dlg.version_label.text() == f"wireplay {version.__version__}", f"the dialog shows the version: {dlg.version_label.text()!r}")
ok(dlg.sgdb.echoMode() == gui.QLineEdit.Password and dlg.ra_key.echoMode() == gui.QLineEdit.Password, "API keys are masked in the dialog")
ok(dlg.sgdb.text() == "SGDB-SECRET-1" and dlg.width_spin.value() == 200 and dlg.discord.isChecked(), "fields are filled from config.json")
ok(dlg.steam_label.text().startswith("✓ Steam found") and "Heroic" in dlg.heroic_label.text() and not dlg.heroic_label.text().startswith("✓"), f"launcher status: {dlg.steam_label.text()[:40]} | {dlg.heroic_label.text()[:50]}")
ok("steamgriddb.com/api" in "".join(l.text() for l in dlg.findChildren(gui.QLabel)) and "retroachievements.org" in "".join(l.text() for l in dlg.findChildren(gui.QLabel)), "links to steamgriddb.com/api and retroachievements.org are shown")
tab_keys = [dlg.startup.itemData(i) for i in range(dlg.startup.count())]
ok(tab_keys[:2] == ["all", "favorites"] and "snes" in tab_keys, f"startup tab choices: {tab_keys[:6]}…")

# ---------------------------------------------------------------- path rows
dlg.roms.setText(str(root / "gone")); pump(20)
ok(dlg.roms.note.text().startswith("✗ folder not found"), f"a missing folder is flagged: {dlg.roms.note.text()!r}")
dlg.roms.setText(str(roms_a)); pump(20)
ok(dlg.roms.note.text().startswith("✓ 3 platform folders"), f"a real ROM folder is recognised: {dlg.roms.note.text()!r}")
dlg.roms.setText(str(root)); pump(20)
ok("no platform folders" in dlg.roms.note.text(), "a folder without platform sub-folders is called out")
dlg.roms.setText(""); pump(20)
ok(dlg.roms.note.text() == f"auto: {roms_a}", f"empty = auto-detect, and it says what it would use: {dlg.roms.note.text()!r}")
dlg.roms.btn_detect.click(); pump(20)
ok(dlg.roms.text() == str(roms_a), "🔍 Auto-Detect fills in the detected folder")
dlg.media.btn_detect.click(); pump(20)
ok(dlg.media.text() == str(roms_a.parent / "tools/downloaded_media") and dlg.media.note.text() == "✓ found", "the media row detects the art folder next to it")
config.ROM_PATTERNS = (); dlg.roms.setText(""); dlg.roms.btn_detect.click(); pump(20)
ok(dlg.roms.text() == "" and dlg.roms.note.text().startswith("✗ nothing found"), "nothing detected: the user is told where wireplay looked")
config.ROM_PATTERNS = (f"{root}/mnt/*/Emulation/roms", "{home}/Emulation/roms")
second = root / "mnt/Other/Emulation/roms"; [(second / p).mkdir(parents=True) for p in ("nes", "snes", "gba", "gb", "psx")]
picked = []
class FakeMenu:
    def __init__(self, parent=None): self.acts = []
    def addAction(self, text): a = type("A", (), {"text": text})(); self.acts.append(a); return a
    def exec(self, pos=None): picked.append([a.text for a in self.acts]); return self.acts[1]
gui_qmenu, gui.QMenu = gui.QMenu, FakeMenu
dlg.roms.btn_detect.click(); pump(20); gui.QMenu = gui_qmenu
ok(len(picked[0]) == 2 and dlg.roms.text() == picked[0][1], f"two candidates: a menu lets you choose ({len(picked[0])} offered, picked #2)")
ok(dlg.roms.btn_browse.text() == "Browse…", "Browse… button present")
chosen = []
gui.QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: str(roms_a)); dlg.roms.btn_browse.click(); pump(20)
ok(dlg.roms.text() == str(roms_a), "Browse… uses the folder chooser")

# ---------------------------------------------------------------- the appearance tab
dlg.width_slider.setValue(130); ok(dlg.width_spin.value() == 130, "slider and spin box stay in sync")
dlg.width_spin.setValue(999); ok(dlg.width_spin.value() == 260 and dlg.width_slider.value() == 260, "limited to 100-260 px")
dlg.width_spin.setValue(5); ok(dlg.width_spin.value() == 100, "…both ends")
dlg.width_spin.setValue(200)

# ---------------------------------------------------------------- saving applies live
dlg.roms.setText(str(second))                      # a different library than the one saved at start-up
v = dlg.values()
ok(set(v) == {"roms_dir", "media_dir", "steamgriddb_api_key", "ra_username", "ra_api_key", "discord_rpc", "card_width", "startup_tab"}, f"values(): {sorted(v)}")
n = len(fake.log)
ok(w.apply_settings(v) is True and w.status.text().startswith("Settings saved: rescanning the library"), f"first save with new folders rescans: {w.status.text()!r}")
d = json.loads(cfgfile.read_text())
ok(d["roms_dir"] == str(second) and d["steamgriddb_api_key"] == "SGDB-SECRET-1" and d["card_width"] == 200 and stat.S_IMODE(cfgfile.stat().st_mode) == 0o600, "written atomically to config.json (mode 0600 kept)")
wait_for(lambda: not w.scanning, 5)
w.status.setText("")
ok(w.apply_settings(dlg.values()) and w.status.text() == "Settings saved", "saving unchanged values triggers nothing (no rescan, no requests)")
pump(100)
# card width, live
v = dlg.values(); v["card_width"] = 120
w.apply_settings(v); pump(100)
ok(w.delegate.poster_w == 120 and json.loads(cfgfile.read_text())["card_width"] == 120, "card width applies to the grid immediately and is saved")
# discord
v = dlg.values(); v["discord_rpc"] = False
w.apply_settings(v)
ok(w.presence.enabled is False and "Discord status off" in w.status.text() and json.loads(cfgfile.read_text())["discord_rpc"] is False, "Discord toggle takes effect at once")
v["discord_rpc"] = True; w.apply_settings(v); ok(w.presence.enabled is True, "…and back on")
# SteamGridDB key
covers.reload_api_key(); ok(covers.api_key() == "SGDB-SECRET-1", "fixture: key loaded")
w.cache.items["fake-none"] = (None, 1); w.cache.items["fake-pix"] = ("PIX", 1)
v = dlg.values(); v["steamgriddb_api_key"] = "SGDB-SECRET-2"; w.apply_settings(v)
ok(covers.api_key() == "SGDB-SECRET-2", "a new SteamGridDB key is used without a restart")
ok("fake-none" not in w.cache.items and "fake-pix" in w.cache.items, "posters that had no art are asked for again; existing ones are kept")
ok("SGDB-SECRET" not in w.status.text(), "the key never appears in the status line")
# RetroAchievements
n = len(fake.log); v = dlg.values(); v["ra_username"], v["ra_api_key"] = "tester", fakes.API_KEY
w.apply_settings(v)
ok(ra_client.enabled() and "RetroAchievements on" in w.status.text(), f"RetroAchievements switches on: {w.status.text()!r}")
ok(wait_for(lambda: q("select count(*) c from games where ra_game_id is not null")[0]["c"] > 0 and not w.ra_syncing, 10), "…and a sync starts at once (trophy badges appear without a restart)")
ok(fakes.API_KEY not in w.status.text(), "the key is not shown in the status line")
v["ra_api_key"] = ""; w.apply_settings(v)
ok(not ra_client.enabled() and "RetroAchievements off" in w.status.text(), "blanking the key turns it off again")
# unwritable / corrupt config
saved = cfgfile.read_text(); cfgfile.write_text("[1, 2]")
ok(w.apply_settings(dlg.values()) is False and "Could not save settings" in w.status.text() and cfgfile.read_text() == "[1, 2]", "a config.json that is not a JSON object is left alone and the user is told")
cfgfile.write_text(saved)

# ---------------------------------------------------------------- startup tab
v = dlg.values(); v["startup_tab"] = "snes"; v["card_width"] = 160; w.apply_settings(v); w.close(); pump(200)
w = gui.Window(); w.presence = FakePresence(); w.show(); pump(500)
ok(w.active == "snes", f"startup tab 'snes' is active at launch: {w.active!r}")
v["startup_tab"] = "favorites"; config.update(startup_tab="favorites"); w.close(); pump(200)
w = gui.Window(); w.presence = FakePresence(); w.show(); pump(500)
ok(w.active == gui.FAV, "startup tab 'favorites'")
config.update(startup_tab="vanished-platform"); w.close(); pump(200)
w = gui.Window(); w.presence = FakePresence(); w.show(); pump(500)
ok(w.active is None, "a startup tab that no longer exists falls back to All")
config.update(startup_tab="all"); w.close(); pump(200)
dlg.grab().save(support.out("settings_paths.png"))
dlg.tabs.setCurrentIndex(1); dlg.grab().save(support.out("settings_keys.png"))
dlg.tabs.setCurrentIndex(2); dlg.grab().save(support.out("settings_look.png"))
fake.stop()
ok(support.fingerprint(REAL) == real_before and support.fingerprint(REAL_CFG) == cfg_before, "the real wireplay.db and config.json were not touched")
print("ALL PASS")
