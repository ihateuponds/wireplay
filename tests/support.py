"""Shared test support. Import this FIRST in every test module.

Every test talks to a throw-away database and config in a temporary directory. WIREPLAY_TEST=1 makes db.connect()
refuse the real wireplay.db, config.update() refuse the real config, and covers.http_get refuse any host except
localhost, so a bug in a test can fail the test but can never touch your library, your keys, or the internet.
"""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
SCRATCH = Path(tempfile.mkdtemp(prefix="wireplay_test_"))
TEST_DB = str(SCRATCH / "wireplay_test.db")
TEST_CFG = str(SCRATCH / "config.json")

os.environ.update(QT_QPA_PLATFORM=os.environ.get("QT_QPA_PLATFORM", "offscreen"), WIREPLAY_TEST="1", WIREPLAY_DB=TEST_DB,
                  WIREPLAY_CONFIG=TEST_CFG, WIREPLAY_CACHE=str(SCRATCH / "cache"))
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
STASH = SCRATCH / "old"                    # kept for old call sites
OUT = SCRATCH / "out"
OUT.mkdir(exist_ok=True)


def out(name):
    """Where a test may write a screenshot or other artefact."""
    return str(OUT / name)


def fresh_db(library=False):
    """An empty test DB and config (or one filled with fixtures.build_library())."""
    for suffix in ("", "-wal", "-shm"):
        Path(TEST_DB + suffix).unlink(missing_ok=True)
    Path(TEST_CFG).write_text('{"discord_rpc": false}\n')
    if library:
        import fixtures
        return fixtures.build_library()


def fingerprint(path):
    """(mtime, size) of a file, None when it does not exist: lets a test prove it did not touch a real file."""
    try:
        st = Path(path).stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        sys.exit(1)
