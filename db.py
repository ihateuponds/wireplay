"""SQLite storage for wireplay."""
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

REAL_DB_PATH = Path(__file__).with_name("wireplay.db")
# WIREPLAY_DB points tests at a throw-away database. WIREPLAY_TEST=1 makes connect() refuse the real one,
# so a verification script can never record playtime (or launch games from) the user's library.
DB_PATH = Path(os.environ.get("WIREPLAY_DB") or REAL_DB_PATH)
MAX_SESSION_MINUTES = 24 * 60    # a single session is never credited with more than a day
DEFAULT_LABEL = "Default"        # version_label of a game with a single, untagged version

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    base_title   TEXT NOT NULL DEFAULT '',
    version_label TEXT NOT NULL DEFAULT 'Default',
    platform     TEXT NOT NULL,
    path_or_id   TEXT NOT NULL,
    launch_cmd   TEXT NOT NULL,
    is_installed INTEGER NOT NULL DEFAULT 1,
    is_favorite  INTEGER NOT NULL DEFAULT 0,
    total_playtime_minutes INTEGER NOT NULL DEFAULT 0,
    last_played  INTEGER DEFAULT NULL,
    notes        TEXT NOT NULL DEFAULT '',
    custom_args  TEXT NOT NULL DEFAULT '',
    tags         TEXT NOT NULL DEFAULT '',
    UNIQUE (platform, path_or_id)
);
CREATE TABLE IF NOT EXISTS sessions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id          INTEGER NOT NULL,
    start_time       INTEGER NOT NULL,
    end_time         INTEGER,
    duration_minutes INTEGER DEFAULT 0,
    pid              INTEGER,            -- process being followed, with its start time (pid-reuse guard)
    pid_start        INTEGER,
    last_seen        INTEGER,            -- heartbeat: last moment the game was seen running
    FOREIGN KEY (game_id) REFERENCES games(id)
);
CREATE INDEX IF NOT EXISTS sessions_game ON sessions (game_id);
CREATE INDEX IF NOT EXISTS sessions_open ON sessions (end_time) WHERE end_time IS NULL;
"""

# Columns added after the first release: name -> definition. _migrate() adds whichever are missing.
ADDED_COLUMNS = {
    "base_title": "TEXT NOT NULL DEFAULT ''",
    "version_label": "TEXT NOT NULL DEFAULT 'Default'",
    "is_favorite": "INTEGER NOT NULL DEFAULT 0",
    "total_playtime_minutes": "INTEGER NOT NULL DEFAULT 0",
    "last_played": "INTEGER DEFAULT NULL",
    "notes": "TEXT NOT NULL DEFAULT ''",
    "custom_args": "TEXT NOT NULL DEFAULT ''",
    "tags": "TEXT NOT NULL DEFAULT ''",
    # HowLongToBeat (hours) + synopsis; meta_checked = when the lookup last ran, hit or miss
    "hltb_main": "REAL DEFAULT NULL",
    "hltb_extra": "REAL DEFAULT NULL",
    "hltb_complete": "REAL DEFAULT NULL",
    "synopsis": "TEXT DEFAULT ''",
    "release_year": "TEXT DEFAULT ''",
    "meta_checked": "INTEGER DEFAULT NULL",
    # RetroAchievements: matched set id, totals, hardcore points, 100% flag, last refresh
    "ra_game_id": "INTEGER DEFAULT NULL",
    "ra_total": "INTEGER NOT NULL DEFAULT 0",
    "ra_unlocked": "INTEGER NOT NULL DEFAULT 0",
    "ra_points": "INTEGER NOT NULL DEFAULT 0",
    "ra_mastered": "INTEGER NOT NULL DEFAULT 0",
    "ra_checked": "INTEGER DEFAULT NULL",
    # ROM-hash matching: the RetroAchievements-style MD5, when RA was last asked about it, and the verdict
    # (1 = this exact ROM belongs to ra_game_id, -1 = RA has no set for this exact ROM, 0 = not hashed yet)
    "rom_hash": "TEXT DEFAULT NULL",
    "ra_hash_checked": "INTEGER DEFAULT NULL",
    "ra_verified": "INTEGER NOT NULL DEFAULT 0",
}
SORTS = {
    "alpha": "base_title COLLATE NOCASE, platform, version_label",
    "recent": "last_played IS NULL, last_played DESC, base_title COLLATE NOCASE, platform, version_label",
    "playtime": "total_playtime_minutes DESC, base_title COLLATE NOCASE, platform, version_label",
}


class Game(NamedTuple):
    """One scanned library entry. `source` ('steam' | 'minecraft' | 'heroic' | 'roms') says which scanner found it."""
    title: str
    platform: str
    path_or_id: str
    launch_cmd: str
    source: str
    base_title: str
    version_label: str
    is_installed: int = 1


def source_of(platform: str) -> str:
    """Which scanner owns rows of this platform (the DB does not store the source)."""
    if platform in ("steam", "minecraft"):
        return platform
    return "heroic" if platform == "epic" else "roms"


def connect(path=None):
    """Open the database. WAL + a long busy timeout let the GUI read while a background rescan writes."""
    path = DB_PATH if path is None else path
    if os.environ.get("WIREPLAY_TEST") and str(path) != ":memory:" and Path(path).resolve() == REAL_DB_PATH.resolve():
        raise RuntimeError("WIREPLAY_TEST is set: refusing to open the real wireplay.db")
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        con.executescript(SCHEMA)
        _migrate(con)
    except sqlite3.Error:
        con.close()
        raise
    return con


@contextmanager
def session(path=None):
    """`with db.session() as con:` - always closed, even when the body raises."""
    con = connect(path)
    try:
        yield con
    finally:
        con.close()


def _migrate(con):
    """Add columns that older databases lack (version stacking, favorites, playtime, notes, ...)."""
    cols = {r["name"] for r in con.execute("PRAGMA table_info(games)")}
    with con:
        for name, definition in ADDED_COLUMNS.items():
            if name not in cols:
                con.execute(f"ALTER TABLE games ADD COLUMN {name} {definition}")
                if name == "base_title":
                    con.execute("UPDATE games SET base_title = title")


def sync(con, games, sources):
    """Upsert scanned games; mark rows of the scanned `sources` that vanished as not installed.

    games:   iterable of Game (or equivalent 7/8-tuples)
    sources: names of the scanners that actually completed, so a failed scan or an unmounted
             drive never marks another source's rows (or its own) as gone.
    One transaction: a crash half-way leaves the previous state intact.
    """
    rows = [Game(*g) for g in games]
    seen = {(g.platform, g.path_or_id) for g in rows}
    with con:
        con.executemany(
            """INSERT INTO games (title, platform, path_or_id, launch_cmd, is_installed, base_title, version_label)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (platform, path_or_id) DO UPDATE SET
                   title = excluded.title,
                   base_title = excluded.base_title,
                   version_label = excluded.version_label,
                   launch_cmd = excluded.launch_cmd,
                   is_installed = excluded.is_installed""",
            [(g.title, g.platform, g.path_or_id, g.launch_cmd, g.is_installed, g.base_title, g.version_label)
             for g in rows],
        )
        gone = [
            (r["id"],)
            for r in con.execute("SELECT id, platform, path_or_id FROM games WHERE is_installed = 1")
            if source_of(r["platform"]) in sources and (r["platform"], r["path_or_id"]) not in seen
        ]
        con.executemany("UPDATE games SET is_installed = 0 WHERE id = ?", gone)


def get_games(con, installed_only=True, sort_by="alpha", favorites_only=False):
    """Rows ordered by sort_by ('alpha' | 'recent' | 'playtime'); optionally only favorites / installed."""
    if sort_by not in SORTS:
        raise ValueError(f"unknown sort {sort_by!r}; expected one of {sorted(SORTS)}")
    where = [c for c, on in (("is_installed = 1", installed_only), ("is_favorite = 1", favorites_only)) if on]
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    return con.execute(
        f"SELECT * FROM games {clause} ORDER BY is_installed DESC, {SORTS[sort_by]}"
    ).fetchall()


def get_game(con, game_id):
    return con.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()


def toggle_favorite(con, base_title, platform):
    """Flip the favorite flag of a whole stacked group (every version of base_title on platform).

    Returns the new state (True = favorite), or None if no such game. A mixed group becomes favorite.
    """
    with con:
        rows = con.execute(
            "SELECT is_favorite FROM games WHERE platform = ? AND base_title = ? COLLATE NOCASE",
            (platform, base_title),
        ).fetchall()
        if not rows:
            return None
        new = 0 if all(r["is_favorite"] for r in rows) else 1
        con.execute("UPDATE games SET is_favorite = ? WHERE platform = ? AND base_title = ? COLLATE NOCASE",
                    (new, platform, base_title))
    return bool(new)


def record_session(con, game_id, elapsed_minutes):
    """Add a finished play session without a sessions row: playtime += minutes (never negative), last_played = now."""
    with con:
        con.execute(
            "UPDATE games SET total_playtime_minutes = total_playtime_minutes + ?, last_played = ? WHERE id = ?",
            (max(0, int(elapsed_minutes)), int(time.time()), game_id),
        )


# ---------------------------------------------------------------- play sessions

def start_session(con, game_id, now=None, pid=None, pid_start=None):
    """Open a session row for a game that was just launched. Returns its id."""
    now = int(time.time() if now is None else now)
    with con:
        cur = con.execute(
            "INSERT INTO sessions (game_id, start_time, pid, pid_start, last_seen) VALUES (?, ?, ?, ?, ?)",
            (game_id, now, pid, pid_start, now),
        )
    return cur.lastrowid


def touch_session(con, session_id, now=None, pid=None, pid_start=None):
    """Heartbeat: the game was still running at `now`. Also remembers the followed pid when it is known."""
    now = int(time.time() if now is None else now)
    with con:
        if pid is None:
            con.execute("UPDATE sessions SET last_seen = ? WHERE id = ? AND end_time IS NULL", (now, session_id))
        else:
            con.execute("UPDATE sessions SET last_seen = ?, pid = ?, pid_start = ? WHERE id = ? AND end_time IS NULL",
                        (now, pid, pid_start, session_id))


def finish_session(con, session_id, minutes, end_time=None):
    """Close an open session: set end_time and duration, add the minutes to the game's total, set last_played.

    One transaction, and a no-op for a session that is already closed, so a duplicate finish can never
    count playtime twice. Returns the minutes credited (0 when nothing was done).
    """
    end = int(time.time() if end_time is None else end_time)
    with con:
        row = con.execute("SELECT game_id, start_time FROM sessions WHERE id = ? AND end_time IS NULL",
                          (session_id,)).fetchone()
        if row is None:
            return 0
        minutes = min(max(0, int(minutes)), MAX_SESSION_MINUTES)
        end = max(end, row["start_time"])
        con.execute("UPDATE sessions SET end_time = ?, duration_minutes = ?, last_seen = ? WHERE id = ?",
                    (end, minutes, end, session_id))
        con.execute("UPDATE games SET total_playtime_minutes = total_playtime_minutes + ?, "
                    "last_played = MAX(COALESCE(last_played, 0), ?) WHERE id = ?",     # never moves backwards
                    (minutes, end, row["game_id"]))
    return minutes


def discard_session(con, session_id):
    """Close a session that never was one (the launch failed at once): zero duration, game totals untouched."""
    with con:
        con.execute("UPDATE sessions SET end_time = start_time, duration_minutes = 0 WHERE id = ? AND end_time IS NULL",
                    (session_id,))


def open_sessions(con):
    """Sessions that were never closed (wireplay quit or crashed while the game ran), oldest first."""
    return con.execute("SELECT * FROM sessions WHERE end_time IS NULL ORDER BY start_time").fetchall()


def session_history(con, game_id, limit=20):
    return con.execute("SELECT * FROM sessions WHERE game_id = ? AND end_time IS NOT NULL AND duration_minutes > 0 "
                       "ORDER BY start_time DESC LIMIT ?", (game_id, limit)).fetchall()


# ---------------------------------------------------------------- tags

MAX_TAGS, MAX_TAG_LEN = 20, 32


def parse_tags(text):
    """Comma-separated text (or a list) -> clean list: trimmed, spaces collapsed, no '#', no duplicates (any case)."""
    parts = text if isinstance(text, (list, tuple)) else str(text or "").split(",")
    out, seen = [], set()
    for part in parts:
        tag = " ".join(str(part).replace("#", " ").split())[:MAX_TAG_LEN].strip()
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            out.append(tag)
    return out[:MAX_TAGS]


def join_tags(tags):
    return ", ".join(parse_tags(tags))


def update_group_tags(con, base_title, platform, tags):
    """Set the tags of a whole stacked group (every version of base_title on platform). Returns the saved list."""
    clean = parse_tags(tags)
    with con:
        con.execute("UPDATE games SET tags = ? WHERE platform = ? AND base_title = ? COLLATE NOCASE",
                    (", ".join(clean), platform, base_title))
    return clean


def tag_counts(con):
    """{tag: number of games} over all rows (tag spelled as first seen), most used first."""
    counts, names = {}, {}
    for r in con.execute("SELECT tags FROM games WHERE tags != ''"):
        for tag in parse_tags(r["tags"]):
            names.setdefault(tag.lower(), tag)
            counts[tag.lower()] = counts.get(tag.lower(), 0) + 1
    return {names[k]: counts[k] for k in sorted(counts, key=lambda k: (-counts[k], k))}


# ---------------------------------------------------------------- HowLongToBeat metadata

META_RETRY_SECS = 30 * 86400     # a lookup that found nothing is tried again after 30 days; a hit is kept for good


def update_game_metadata(con, game_id, main, extra, comp, synopsis, year):
    """Store HowLongToBeat hours (REAL, None = unknown), the synopsis and the release year for one row."""
    with con:
        con.execute(
            "UPDATE games SET hltb_main = ?, hltb_extra = ?, hltb_complete = ?, synopsis = ?, release_year = ?, "
            "meta_checked = ? WHERE id = ?",
            (main, extra, comp, synopsis or "", str(year or ""), int(time.time()), game_id),
        )


def mark_metadata_checked(con, game_ids):
    """A lookup found nothing: remember that (without touching any data) so it is not repeated on every view."""
    with con:
        con.executemany("UPDATE games SET meta_checked = ? WHERE id = ?", [(int(time.time()), i) for i in game_ids])


def metadata_missing(row, now=None):
    """True when this row should be looked up: never checked, or a miss older than META_RETRY_SECS."""
    checked = row["meta_checked"]
    if checked is None:
        return True
    has_data = any(row[k] for k in ("hltb_main", "hltb_extra", "hltb_complete", "synopsis"))
    return not has_data and (now or time.time()) - checked > META_RETRY_SECS


def find_cached_metadata(con, title, exclude_id=None):
    """Another row with the same title (typically the same game on another platform) that already has HowLongToBeat
    data, or None. HLTB data belongs to the game, not the platform, so a Steam lookup also serves the Switch version."""
    return con.execute(
        "SELECT * FROM games WHERE title = ? COLLATE NOCASE AND id != ? AND "
        "(hltb_main IS NOT NULL OR hltb_extra IS NOT NULL OR hltb_complete IS NOT NULL OR synopsis != '') LIMIT 1",
        (title, exclude_id or 0),
    ).fetchone()


# ---------------------------------------------------------------- RetroAchievements cache

def set_ra_match(con, game_id, ra_game_id, total):
    """Remember which RetroAchievements set a row belongs to (and how many achievements it has)."""
    with con:
        con.execute("UPDATE games SET ra_game_id = ?, ra_total = ? WHERE id = ?", (ra_game_id, int(total), game_id))


def clear_ra_matches(con, game_ids):
    """Forget a RetroAchievements match (the title no longer matches a set): no badge, no counts."""
    with con:
        con.executemany("UPDATE games SET ra_game_id = NULL, ra_total = 0, ra_unlocked = 0, ra_points = 0, "
                        "ra_mastered = 0, ra_checked = NULL WHERE id = ?", [(i,) for i in game_ids])


def update_ra_progress(con, ra_game_id, total, unlocked, points=None, mastered=None):
    """Store the user's progress for a RetroAchievements set on every row matched to it.

    points (hardcore points) is only known from the per-game call; None keeps what is stored.
    mastered defaults to 'every achievement unlocked'.
    """
    total, unlocked = int(total), int(unlocked)
    if mastered is None:
        mastered = total > 0 and unlocked >= total
    with con:
        if points is None:
            con.execute("UPDATE games SET ra_total = ?, ra_unlocked = ?, ra_mastered = ?, ra_checked = ? "
                        "WHERE ra_game_id = ?", (total, unlocked, int(bool(mastered)), int(time.time()), ra_game_id))
        else:
            con.execute("UPDATE games SET ra_total = ?, ra_unlocked = ?, ra_points = ?, ra_mastered = ?, "
                        "ra_checked = ? WHERE ra_game_id = ?",
                        (total, unlocked, int(points), int(bool(mastered)), int(time.time()), ra_game_id))


RA_MISS_RETRY_SECS = 30 * 86400    # "RA has no set for this ROM" is asked again after a month (hash lists grow)


def ra_candidates(con, platforms):
    """Rows of the given platforms still decided by title (not settled by their ROM hash), for matching."""
    marks = ",".join("?" * len(platforms))
    return con.execute(f"SELECT id, platform, title, base_title, ra_game_id FROM games "
                       f"WHERE platform IN ({marks}) AND ra_verified = 0", tuple(platforms)).fetchall() if platforms else []


def set_rom_hash(con, game_id, digest):
    with con:
        con.execute("UPDATE games SET rom_hash = ? WHERE id = ?", (digest, game_id))


def set_ra_verified(con, game_id, ra_game_id, total=0):
    """Record what the ROM hash proved: a match (ra_game_id given) or 'RetroAchievements has no set for this ROM'.

    A match copies the progress numbers other rows of the same set already have (a fetch refines them later); a
    non-match clears any title-based badge, because this exact ROM cannot earn achievements.
    """
    now = int(time.time())
    with con:
        if ra_game_id is None:
            con.execute("UPDATE games SET ra_game_id = NULL, ra_total = 0, ra_unlocked = 0, ra_points = 0, ra_mastered = 0, "
                        "ra_verified = -1, ra_hash_checked = ? WHERE id = ?", (now, game_id))
            return
        known = con.execute("SELECT ra_unlocked, ra_points, ra_mastered, ra_checked FROM games WHERE ra_game_id = ? AND id != ? "
                            "AND ra_checked IS NOT NULL LIMIT 1", (ra_game_id, game_id)).fetchone()
        unlocked, points, mastered, checked = (known["ra_unlocked"], known["ra_points"], known["ra_mastered"], known["ra_checked"]) \
            if known else (0, 0, 0, None)
        con.execute("UPDATE games SET ra_game_id = ?, ra_total = ?, ra_unlocked = ?, ra_points = ?, ra_mastered = ?, ra_checked = ?, "
                    "ra_verified = 1, ra_hash_checked = ? WHERE id = ?",
                    (ra_game_id, int(total), unlocked, points, mastered, checked, now, game_id))


def update_game_notes(con, game_id, notes):
    with con:
        con.execute("UPDATE games SET notes = ? WHERE id = ?", (notes, game_id))


def update_custom_args(con, game_id, args):
    with con:
        con.execute("UPDATE games SET custom_args = ? WHERE id = ?", (args.strip(), game_id))


def installed(con):
    return get_games(con, installed_only=True)


def counts(con):
    return {
        r["platform"]: r["n"]
        for r in con.execute(
            "SELECT platform, COUNT(*) n FROM games WHERE is_installed = 1 GROUP BY platform ORDER BY n DESC"
        )
    }
