import support
support.fresh_db()
import os, sys, tempfile, time, json, stat
from pathlib import Path
from support import ok
os.environ["WIREPLAY_CACHE"] = tempfile.mkdtemp(prefix="wp_cache_", dir="/tmp")
import db, config
REAL = Path(db.REAL_DB_PATH); real_before = support.fingerprint(REAL)
REAL_CFG = config.REAL_CONFIG_PATH; cfg_before = support.fingerprint(REAL_CFG)
import covers, engine, scanners, discord_rpc
discord_rpc.socket_candidates = lambda: []
covers.fetch_poster = lambda rows: None
root = Path(tempfile.mkdtemp(prefix="wp_wiz_", dir="/tmp")); home = root / "home"; home.mkdir(); os.environ["HOME"] = str(home)
roms = root / "mnt/SD/Emulation/roms"
for plat, names in (("snes", ["Chrono Trigger (USA).sfc", "EarthBound (USA).sfc"]), ("gba", ["Metroid Fusion (USA).gba"]), ("nes", [])): 
    (roms / plat).mkdir(parents=True); [(roms / plat / n).write_bytes(b"rom" * 10) for n in names]
(roms.parent / "tools/downloaded_media").mkdir(parents=True)
config.ROM_PATTERNS = (f"{root}/mnt/*/Emulation/roms", "{home}/Emulation/roms")
scanners.GDLAUNCHER_INSTANCES = root / "no-minecraft"        # that scanner's folders are fixed at import time, from the real home
from PySide6.QtWidgets import QApplication, QDialog
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
class FakePresence:
    enabled = True
    def set(self, *a): pass
    def clear(self): pass
    def shutdown(self, timeout=1.5): pass
    def set_enabled(self, on): self.enabled = on
cfgfile = Path(os.environ["WIREPLAY_CONFIG"]); cfgfile.unlink(missing_ok=True)

# ---------------------------------------------------------------- when does it show?
ok(config.first_run_needed(), "no config.json -> first run")
ok(not (config.CONFIG_PATH.exists()), "…and nothing has created the file yet")
cfgfile.write_text("{}"); ok(not config.first_run_needed(), "an existing config.json (even empty) is not a first run"); cfgfile.unlink()
shown = []
orig_exec = gui.FirstRunWizard.exec
gui.FirstRunWizard.exec = lambda self: (shown.append(self), QDialog.Accepted)[1]
cfgfile.write_text('{"first_run": false}'); ok(gui.run_first_run_wizard() is False and not shown, "an existing install never sees the wizard")
cfgfile.write_text('{"first_run": true}'); ok(gui.run_first_run_wizard() is True and len(shown) == 1, '"first_run": true shows it')
cfgfile.unlink(); shown.clear()

# ---------------------------------------------------------------- the three steps
wiz = gui.FirstRunWizard(); wiz.show(); pump(100)
ok(wiz.pages.count() == 3 and wiz.pages.currentIndex() == 0, "three steps, starting at the welcome page")
ok("Wireplay // Game Launcher" in "".join(l.text() for l in wiz.pages.widget(0).findChildren(gui.QLabel)), "step 1: 'Welcome to Wireplay // Game Launcher'")
ok(wiz.btn_back.isHidden() and wiz.btn_next.text() == "Next →" and wiz.step_label.text().startswith("●   ○   ○"), f"step indicator: {wiz.step_label.text()!r}")
wiz.btn_next.click(); pump(30)
ok(wiz.pages.currentIndex() == 1 and not wiz.btn_back.isHidden(), "Next → goes to step 2, Back appears")
ok(wiz.roms.text() == str(roms), f"step 2: the ROM folder was detected and filled in: {wiz.roms.text()}")
ok(wiz.media.text() == str(roms.parent / "tools/downloaded_media"), "…and so was the art folder")
ok(wiz.roms.note.text().startswith("✓ 3 platform folders"), f"…with a verdict: {wiz.roms.note.text()!r}")
lbls = [l.text() for l in wiz.pages.widget(1).findChildren(gui.QLabel)]
ok(any("Steam" in t for t in lbls) and any("Heroic" in t for t in lbls), "step 2 also lists Steam and Heroic status")
wiz.btn_back.click(); pump(20); ok(wiz.pages.currentIndex() == 0, "Back works")
wiz.btn_next.click(); wiz.btn_next.click(); pump(30)
ok(wiz.pages.currentIndex() == 2 and wiz.btn_next.text().replace("&&", "&") == "Finish & scan" and wiz.step_label.text().startswith("○   ○   ●"), "step 3: the button becomes 'Finish & scan'")
ok(wiz.sgdb.echoMode() == gui.QLineEdit.Password and wiz.discord.isChecked(), "optional keys are masked; Discord is on by default")
wiz.sgdb.setText("MY-SGDB-KEY"); wiz.ra_user.setText("player1"); wiz.ra_key.setText("MY-RA-KEY"); wiz.discord.setChecked(False)
v = wiz.values()
ok(v == {"roms_dir": str(roms), "media_dir": str(roms.parent / "tools/downloaded_media"), "steamgriddb_api_key": "MY-SGDB-KEY", "ra_username": "player1", "ra_api_key": "MY-RA-KEY", "discord_rpc": False}, "values() carries every choice")
wiz.grab().save(support.out("wizard3.png")); wiz.go(1); wiz.grab().save(support.out("wizard2.png")); wiz.go(0); wiz.grab().save(support.out("wizard1.png"))
wiz.go(2)
done = []; wiz.accepted.connect(lambda: done.append(1)); wiz.btn_next.click(); pump(30)
ok(done and wiz.result() == QDialog.Accepted, "Finish accepts the dialog")
ok(wiz.save(finished=True), "save() writes the config")
d = json.loads(cfgfile.read_text())
ok(d == {"first_run": False, **v} and stat.S_IMODE(cfgfile.stat().st_mode) == 0o600, f"config.json created with the choices, first_run=false, mode 0600: {sorted(d)}")
ok(not config.first_run_needed(), "the wizard will not show again")

# ---------------------------------------------------------------- skip
cfgfile.unlink()
w2 = gui.FirstRunWizard(); w2.sgdb.setText("typed-but-skipped"); w2.btn_skip.click(); pump(30)
ok(w2.result() == QDialog.Rejected, "Skip closes the dialog")
w2.save(finished=False); d = json.loads(cfgfile.read_text())
ok(d["first_run"] is False and d["roms_dir"] == str(roms) and "steamgriddb_api_key" not in d, f"skipping keeps detection but none of the typed values: {sorted(d)}")
cfgfile.unlink()
# closing the window with the X is the same as Skip
w3 = gui.FirstRunWizard(); w3.close(); w3.save(finished=w3.result() == QDialog.Accepted)
ok(json.loads(cfgfile.read_text())["first_run"] is False, "closing the wizard counts as Skip: it does not nag again")
# nothing detected anywhere
cfgfile.unlink(); config.ROM_PATTERNS = ()
w4 = gui.FirstRunWizard(); ok(w4.roms.text() == "" and w4.media.text() == "", "no ROM folder anywhere: fields stay empty, the wizard still works (Browse…)")
config.ROM_PATTERNS = (f"{root}/mnt/*/Emulation/roms", "{home}/Emulation/roms")

# ---------------------------------------------------------------- fresh install end to end: wizard -> scan -> grid
cfgfile.unlink(missing_ok=True)
for suf in ("", "-wal", "-shm"): Path(support.TEST_DB + suf).unlink(missing_ok=True) if False else None
support.fresh_db(); cfgfile.unlink(missing_ok=True)
ok(len(db.connect().execute("select 1 from games").fetchall()) == 0, "fresh install: empty library")
gui.FirstRunWizard.exec = lambda self: (self.roms.setText(str(roms)), self.discord.setChecked(False), QDialog.Accepted)[1]
ok(gui.run_first_run_wizard() is True, "the wizard runs before the window")
ok(json.loads(cfgfile.read_text())["roms_dir"] == str(roms) and not config.first_run_needed(), "…and saved the config")
w = gui.Window(); w.resize(1100, 700); w.presence = FakePresence(); w.show(); pump(100)
ok(w.scanning or w.all_groups, "the main window starts the initial scan by itself")
ok(wait_for(lambda: not w.scanning, 15), "initial scan finished")
titles = sorted(g["base"] for g in w.all_groups)
ok(titles == ["Chrono Trigger", "EarthBound", "Metroid Fusion"], f"the launcher grid shows the scanned library: {titles}")
ok(set(w.tab_buttons) >= {gui.FAV, None, "snes", "gba"} and w.model.rowCount() == 3, f"tabs built from the new library: {[k for k in w.tab_buttons][:6]}")
ok("games ready" in w.status.text() or "Rescan complete" in w.status.text(), f"status: {w.status.text()!r}")
w.grab().save(support.out("fresh_install.png"))
w.close(); pump(200)
gui.FirstRunWizard.exec = orig_exec
ok(support.fingerprint(REAL) == real_before and support.fingerprint(REAL_CFG) == cfg_before, "the real wireplay.db and config.json were not touched")
print("ALL PASS")
