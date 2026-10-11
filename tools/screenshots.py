#!/usr/bin/env python3
"""Render the README screenshots from a FICTIONAL demo library (made-up titles, procedurally drawn covers).

    python3 tools/screenshots.py            # writes docs/screenshots/*.png

Nothing here reads or writes your real library, config, caches or network: it reuses the test sandbox
(tests/support.py: throw-away database/config/cache, WIREPLAY_TEST=1), so you can run it any time.
"""
import hashlib
import os
import socket
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tests"))
import support                                    # noqa: E402  (sets the sandbox environment; must come first)

support.fresh_db()

import config                                     # noqa: E402
import covers                                     # noqa: E402
import db                                         # noqa: E402
import engine                                     # noqa: E402
from PySide6.QtCore import QRectF, Qt              # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPolygonF  # noqa: E402
from PySide6.QtCore import QPointF                 # noqa: E402
from PySide6.QtWidgets import QApplication         # noqa: E402

OUT = HERE.parent / "docs" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)
NOW = int(time.time())
DAY = 86400

# (platform, title, installed, favorite, tags, playtime minutes, days since played, hltb (main, extra, 100%), year, synopsis, trophies (done, total))
STEAM = [
    ("Neon Drifter", 1, 1, "Racing, Chill", 1260, 0, (9, 14, 31), "2022", "Slide a hover-bike through a rain-soaked megacity. Beat ghost times and unlock district-wide time trials.", None),
    ("Starfall Tactics", 1, 0, "Strategy, Currently Playing", 2985, 1, (38, 70, 160), "2021", "Command a fleet of salvaged ships in turn-based battles. Every campaign ends differently.", None),
    ("Pixel Alchemist", 1, 1, "Puzzle, Co-op", 410, 3, (6, 9, 15), "2023", "Brew potions on a grid and chain reactions for score. Two players can share one cauldron.", None),
    ("Hollow Lantern", 1, 0, "RPG", 0, None, (22, 40, 75), "2020", "Carry a failing lantern through a drowned kingdom. Light is both your weapon and your health.", None),
    ("Rustborn", 1, 0, "RPG", 155, 12, (30, 55, 110), "2019", "A wasteland fable about a mechanic and a very old robot. Fix things, or break them better.", None),
    ("Glass Harbor", 1, 0, "Chill", 0, None, (4, 6, 9), "2024", "Rebuild a seaside town one window at a time. No timers, no enemies.", None),
    ("Moth & Ember", 0, 0, "", 0, None, None, "", "", None),
    ("Tidal Circuit", 0, 0, "", 0, None, None, "", "", None),
]
EPIC = [("Skyline Courier", 0), ("Ashes of Orion", 0)]
RETRO = [   # platform, title, installed, favorite, playtime, days, trophies
    ("snes", "Dragon Quarry", 1, 1, 1850, 2, (31, 70)),
    ("snes", "Star Courier", 1, 0, 300, 20, (4, 52)),
    ("snes", "Mystic Knights", 1, 0, 0, None, (0, 66)),
    ("nes", "Block Brigade", 1, 0, 95, 40, (12, 15)),
    ("nes", "Cave Runner", 1, 0, 0, None, (0, 27)),
    ("n64", "Turbo Bay 64", 1, 0, 640, 5, (9, 39)),
    ("gba", "Wing Racers", 1, 0, 0, None, None),
    ("gb", "Tiny Tamers", 1, 0, 0, None, (0, 41)),
    ("psx", "Neon Circuit", 1, 0, 770, 1, (22, 48)),
    ("gc", "Garden Guardians", 1, 0, 0, None, (0, 33)),
]
STACK_BASE = "Critter Quest"                      # a big family of versions for the collection browser
STACK_LABELS = (["Original"] + [f"{lang}, v1.{n}" for lang in ("De", "Es", "Fr", "It", "Pt") for n in (0, 2)]
                + [f"Hack {n}, Hack" for n in range(1, 9)] + ["Subset - Speedrun", "Rev 1", "v2.0"])


def hue_color(seed, s=0.6, v=0.75, offset=0):
    h = int(hashlib.sha1(seed.encode()).hexdigest()[:6], 16) % 360
    return QColor.fromHsv((h + offset) % 360, int(255 * s), int(255 * v))


def draw_cover(title, path):
    """A 600x900 poster: two-colour gradient, a few big shapes, the title. Distinct per title, obviously not real art."""
    img = QImage(600, 900, QImage.Format_RGB32)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, 0, 900)
    g.setColorAt(0, hue_color(title, 0.75, 0.55)); g.setColorAt(1, hue_color(title, 0.9, 0.2, 40))
    p.fillRect(0, 0, 600, 900, g)
    seed = int(hashlib.sha1(title.encode()).hexdigest()[:8], 16)
    for i in range(5):
        c = hue_color(title, 0.5, 0.95, 30 * (i + 1)); c.setAlpha(120)
        p.setBrush(c); p.setPen(Qt.NoPen)
        x, y, r = (seed >> (i * 5)) % 480 + 60, (seed >> (i * 3)) % 420 + 120, 80 + (seed >> (i * 4)) % 140
        if i % 2:
            p.drawEllipse(QPointF(x, y), r, r)
        else:
            p.drawPolygon(QPolygonF([QPointF(x, y - r), QPointF(x + r, y + r), QPointF(x - r, y + r)]))
    p.setPen(QColor(255, 255, 255, 235))
    font = QFont("DejaVu Sans", 46, QFont.Bold)
    p.setFont(font)
    p.drawText(QRectF(40, 250, 520, 320), Qt.AlignHCenter | Qt.AlignVCenter | Qt.TextWordWrap, title.upper())
    p.end()
    img.save(str(path), "PNG")


def build_demo_library():
    rows, meta = [], {}
    for title, installed, fav, tags, minutes, days, hltb, year, synopsis, _ in STEAM:
        aid = 5000 + len(rows)
        rows.append(db.Game(title, "steam", str(aid), f"steam steam://rungameid/{aid}", "steam", title, db.DEFAULT_LABEL, installed))
        meta[title] = (fav, tags, minutes, days, hltb, year, synopsis, None)
    for title, installed in EPIC:
        rows.append(db.Game(title, "epic", f"demo{len(rows):04d}", "heroic heroic://launch/legendary/x", "heroic", title, db.DEFAULT_LABEL, installed))
    for plat, title, installed, fav, minutes, days, trophies in RETRO:
        rows.append(db.Game(title, plat, f"/demo/roms/{plat}/{title}.rom", "xdg-open x", "roms", title, db.DEFAULT_LABEL, installed))
        meta[title] = (fav, "", minutes, days, None, "", "", trophies)
    for label in STACK_LABELS:
        title = STACK_BASE if label == "Original" else f"{STACK_BASE} [{label}]"
        rows.append(db.Game(title, "gba", f"/demo/roms/gba/{title}.gba", "xdg-open x", "roms", STACK_BASE, label, 1))
    with db.session() as con:
        db.sync(con, rows, {"steam", "heroic", "roms"})
        ids = {r["title"]: r["id"] for r in con.execute("SELECT id, title FROM games")}
        for title, (fav, tags, minutes, days, hltb, year, synopsis, trophies) in meta.items():
            gid = ids[title]
            if fav:
                con.execute("UPDATE games SET is_favorite = 1 WHERE id = ?", (gid,))
            if tags:
                row = con.execute("SELECT platform, base_title FROM games WHERE id = ?", (gid,)).fetchone()
                db.update_group_tags(con, row["base_title"], row["platform"], tags)
            if minutes:                                                     # a few sessions that add up to the playtime
                end = NOW - (days or 0) * DAY
                for k, part in enumerate((minutes // 2, minutes // 3, minutes - minutes // 2 - minutes // 3)):
                    sid = db.start_session(con, gid, now=end - (k * 2 + 1) * DAY - part * 60)
                    db.finish_session(con, sid, part, end_time=end - (k * 2 + 1) * DAY)
                con.execute("UPDATE games SET last_played = ? WHERE id = ?", (end, gid))
            if hltb:
                db.update_game_metadata(con, gid, hltb[0], hltb[1], hltb[2], synopsis, year)
            if trophies:
                db.set_ra_match(con, gid, 9000 + gid, trophies[1])
                db.update_ra_progress(con, 9000 + gid, trophies[1], trophies[0], points=trophies[0] * 5)
        con.commit()
        for r in con.execute("SELECT * FROM games WHERE base_title = ? AND platform = 'gba'", (STACK_BASE,)):
            gid = r["id"]
            if r["version_label"] == "De, v1.2":
                con.execute("UPDATE games SET notes = 'Best translation of the three. Use the sound patch.', custom_args = '--fullscreen' WHERE id = ?", (gid,))
            if r["version_label"] in ("Original", "De, v1.2", "Fr, v1.0"):
                sid = db.start_session(con, gid, now=NOW - 3 * DAY - 2400)
                db.finish_session(con, sid, 40 if r["version_label"] != "Original" else 120, end_time=NOW - 3 * DAY)
                db.set_ra_match(con, gid, 9777, 54)
                db.update_ra_progress(con, 9777, 54, 18 if r["version_label"] == "Original" else 0, points=90)
        con.commit()
        rows_all = con.execute("SELECT * FROM games").fetchall()
    seen = set()
    for r in rows_all:                                                      # one poster per group, found as a "custom" cover
        stem = covers.custom_stem(r)
        if stem not in seen:
            seen.add(stem)
            draw_cover(r["base_title"], covers.CACHE_DIR / f"{stem}.png")
    return ids


def main():
    app = QApplication([])
    import gui
    app.setStyleSheet(gui.QSS)
    covers.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    state = support.SCRATCH / "state" / "syncthing"; state.mkdir(parents=True)             # a fake Syncthing for the badge
    saves = support.SCRATCH / "saves"; saves.mkdir()
    (saves / f"{STACK_BASE}.srm").write_bytes(b"save")
    (state / "config.xml").write_text(f'<configuration><folder id="s" label="Saves" path="{saves}"><device id="A"/><device id="B"/></folder>'
                                      f'<device id="A" name="{socket.gethostname()}"/><device id="B" name="Handheld"/></configuration>')
    os.environ["XDG_STATE_HOME"] = str(support.SCRATCH / "state")
    ids = build_demo_library()
    config.update(ra_username="demo", ra_api_key="demo-key-not-real", discord_rpc=False, first_run=False)   # enables the RA card; never contacts RA
    engine.SCANNERS = ()

    def pump(ms):
        end = time.time() + ms / 1000
        while time.time() < end:
            app.processEvents(); time.sleep(0.004)

    class Quiet:                                                            # no Discord, whatever the machine
        enabled = False
        def set(self, *a): pass
        def clear(self): pass
        def shutdown(self, timeout=1.5): pass
        def set_enabled(self, on): pass

    w = gui.Window(); w.presence = Quiet(); w.resize(1360, 860); w.show(); pump(1500)
    # 1. the library grid with the shelf and the quick filters
    w.grab().save(str(OUT / "grid.png")); print("grid.png")
    # 2. a game's details: HowLongToBeat, trophies, save sync, sessions, tags
    w.search.setText("starfall"); pump(300)
    w.search.clear(); w.set_tab("steam"); pump(300)
    group = next(g for g in w.model.groups if g["base"] == "Starfall Tactics")
    w.view.setCurrentIndex(w.model.index(w.model.groups.index(group))); w.btn_details.setChecked(True); pump(1200)
    w.drawer.stats.click(); pump(300)
    w.grab().save(str(OUT / "drawer.png")); print("drawer.png")
    # 3. a retro game with trophies and a synced save
    w.set_tab("gba"); pump(300)
    stack = next(g for g in w.model.groups if g["base"] == STACK_BASE)
    w.view.setCurrentIndex(w.model.index(w.model.groups.index(stack))); pump(1200)
    w.drawer.select_version(next(r["id"] for r in stack["rows"] if r["version_label"] == "Original")); pump(800)
    w.grab().save(str(OUT / "retro.png")); print("retro.png")
    # 4. the collection browser for the big family
    with db.session() as con:
        rows = con.execute("SELECT * FROM games WHERE base_title = ? AND platform = 'gba'", (STACK_BASE,)).fetchall()
    dlg = gui.CollectionDialog(stack, rows, w); dlg.resize(960, 640)
    dlg.search.setText(""); dlg.view.setCurrentIndex(dlg.model.index(next(i for i, it in enumerate(dlg.model.items) if it["label"] == "De, v1.2"))); pump(200)
    dlg.grab().save(str(OUT / "collection.png")); print("collection.png")
    # 5. settings, 6. the first-run wizard
    st = gui.SettingsDialog(w, [("steam", "Steam"), ("snes", "snes")]); st.roms.setText("~/Emulation/roms"); st.resize(700, 460); pump(100)
    st.grab().save(str(OUT / "settings.png")); print("settings.png")
    config.update(first_run=True)
    wiz = gui.FirstRunWizard(); wiz.go(1); wiz.resize(700, 520); pump(100)
    wiz.grab().save(str(OUT / "wizard.png")); print("wizard.png")
    w.close()
    os._exit(0)


if __name__ == "__main__":
    main()
