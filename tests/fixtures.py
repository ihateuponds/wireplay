"""A synthetic game library for tests: made-up files and names, no dependency on anyone's real collection."""
import db

G = db.Game
DEFAULT = db.DEFAULT_LABEL
LANGS = ("De", "Es", "Fr", "It", "Pl", "Pt", "Ru")


def _rom(plat, title, label=DEFAULT, base=None, filename=None, installed=1):
    base = base or title
    path = f"/nonexistent/roms/{plat}/{filename or title}.rom"
    return G(title if label == DEFAULT else f"{title} [{label}]", plat, path, f"xdg-open {path}", "roms", base, label, installed)


def build_library():
    """Fill the test DB (db.DB_PATH) and return {name: id}-style handles for the groups tests care about."""
    rows = []
    for i in range(1, 15):                                             # installed Steam games
        rows.append(G(f"Demo Steam Game {i:02d}", "steam", str(1000 + i), f"steam steam://rungameid/{1000 + i}", "steam",
                      f"Demo Steam Game {i:02d}", DEFAULT, 1))
    for i in range(1, 5):                                              # owned but not installed
        rows.append(G(f"Demo Uninstalled {i}", "steam", str(2000 + i), f"steam steam://install/{2000 + i}", "steam",
                      f"Demo Uninstalled {i}", DEFAULT, 0))
    for i in range(1, 4):
        rows.append(G(f"Demo Epic Game {i}", "epic", f"epicapp{i:04d}", f"heroic heroic://launch/legendary/epicapp{i:04d}",
                      "heroic", f"Demo Epic Game {i}", DEFAULT, 0))
    for plat in ("nes", "n64", "psx", "gc", "wii"):
        for i in range(1, 9):
            rows.append(_rom(plat, f"Demo {plat.upper()} Game {i}"))
    # a 27-version stack with translations, hacks, a subset and a revision (the collection-browser case)
    rows.append(_rom("snes", "EarthBound", filename="EarthBound (USA)"))
    for lang in LANGS:
        for extra in ("", ", New Controls, v1.0"):
            label = f"{lang}, v1.0{extra}"
            rows.append(_rom("snes", "EarthBound", label, filename=f"EarthBound ({lang}) (v1.0{extra})"))
    for n in range(1, 8):
        rows.append(_rom("snes", "EarthBound", f"Hack {n}, Hack", filename=f"EarthBound - Hack {n} (Hack)"))
    rows.append(_rom("snes", "EarthBound", "Subset - Low Level Run", filename="EarthBound (Subset - Low Level Run)"))
    rows.append(_rom("snes", "EarthBound", "Rev 1", filename="EarthBound (USA) (Rev 1)"))
    rows.append(_rom("snes", "EarthBound", "v2.0", filename="EarthBound (v2.0)"))
    # a small stack (2 versions) and a few singles
    rows.append(_rom("snes", "Chrono Quest", filename="Chrono Quest (USA)"))
    rows.append(_rom("snes", "Chrono Quest", "Rev 1", filename="Chrono Quest (USA) (Rev 1)"))
    for i in range(1, 7):
        rows.append(_rom("snes", f"Demo SNES Game {i}"))
    for i in range(1, 4):                                              # a platform RetroAchievements does not cover
        rows.append(_rom("switch", f"Demo Switch Game {i}"))
    # a 90-version collection sharing one base title, like a big family of ROM hacks
    def family(title, label):                                           # titles stay as the scanner builds them: no [tag]
        path = f"/nonexistent/roms/gba/{title}.gba"
        return G(title, "gba", path, f"xdg-open {path}", "roms", "Pokémon", label, 1)
    rows.append(family("Pokemon - Emerald Version", "Emerald Version"))
    for n in range(1, 88):
        rows.append(family(f"Pokemon - Demo Hack {n:02d}", f"Demo Hack {n:02d}"))
    for dup in (1, 2):                                                 # the same ROM twice (two folders)
        rows.append(G("Pokémon Modern Emerald", "gba", f"/nonexistent/roms{dup}/gba/Pokémon Modern Emerald (3.5).gba",
                      "xdg-open x", "roms", "Pokémon", "Modern Emerald", 1))
    with db.session() as con:
        db.sync(con, rows, {"steam", "heroic", "roms"})
        ids = {r["title"]: r["id"] for r in con.execute("SELECT id, title FROM games")}
    return ids
