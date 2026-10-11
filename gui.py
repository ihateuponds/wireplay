#!/usr/bin/env python3
"""wireplay GUI - poster grid launcher (PySide6).

  python3 gui.py                          open the launcher
  python3 gui.py --screenshot out.png     render, wait for posters, save a screenshot, exit (for testing)
"""
import hashlib
import os
import random
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import (QAbstractAnimation, QAbstractListModel, QEasingCurve, QModelIndex, QObject, QPoint,
                            QPropertyAnimation, QRect, QRectF, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal)
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QIcon, QImage, QImageReader, QKeySequence, QPainter, QPen,
                           QPixmap, QShortcut)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QCompleter, QDialog,
                               QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit, QListView,
                               QListWidget, QListWidgetItem, QMenu, QProgressBar, QPushButton, QScrollArea, QSizeGrip,
                               QSlider, QSpinBox, QStackedWidget, QStyle, QStyledItemDelegate, QTabWidget, QTextEdit,
                               QVBoxLayout, QWidget)

import config
import covers
import db
import discord_rpc
import engine
import metadata
import ra_client
from version import __version__

BG, PANEL, ACCENT, TEXT, DIM, LINE = "#0e0e10", "#151518", "#e03131", "#e0e0e0", "#80808a", "#26262b"
GOLD = "#f5c518"
DRAWER_W = 320
FAV = "__favorites__"                   # the active-tab value of the ★ Favorites tab
DEFAULT_POSTER_W, MIN_POSTER_W, MAX_POSTER_W, ZOOM_STEP = 160, 100, 260, 15
COLLECTION_MIN = 5                      # stacks with more versions than this get the collection browser
SHELF_POSTER_W = 84                     # the Recently Played shelf uses small, fixed-size cards


def card_metrics(poster_w):
    """(poster_w, poster_h, card_w, card_h) for a poster width; posters keep a 2:3 aspect ratio."""
    poster_h = int(poster_w * 1.5)
    return poster_w, poster_h, poster_w + 16, poster_h + 56


def clamp_poster_w(w):
    return max(MIN_POSTER_W, min(MAX_POSTER_W, int(round(w))))


GROUP_ROLE = Qt.UserRole + 1
ITEM_ROLE = Qt.UserRole + 2

QSS = f"""
* {{ font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace; font-size: 12px; border-radius: 0px; }}
QWidget {{ background: {BG}; color: {TEXT}; }}
#titlebar {{ background: {BG}; border-bottom: 1px solid {LINE}; }}
#logo {{ color: {ACCENT}; font-weight: bold; letter-spacing: 2px; }}
#status {{ color: {DIM}; }}
QPushButton {{ background: {BG}; color: {TEXT}; border: 1px solid {LINE}; padding: 4px 10px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:checked {{ background: {ACCENT}; border-color: {ACCENT}; color: {BG}; font-weight: bold; }}
QPushButton#winbtn {{ border: none; padding: 4px 12px; }}
QPushButton#winbtn:hover {{ background: {ACCENT}; color: {BG}; }}
QLineEdit {{ background: {PANEL}; border: 1px solid {LINE}; padding: 6px 8px; selection-background-color: {ACCENT}; }}
QLineEdit:focus {{ border-color: {ACCENT}; }}
QListView, QListWidget {{ background: {BG}; border: none; outline: 0; }}
QListWidget {{ border: 1px solid {LINE}; background: {PANEL}; }}
QListWidget::item {{ padding: 6px 8px; }}
QListWidget::item:selected {{ background: {ACCENT}; color: {BG}; }}
QScrollArea {{ border: none; }}
QScrollBar:vertical {{ background: {BG}; width: 8px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {LINE}; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {ACCENT}; }}
QScrollBar:horizontal {{ background: {BG}; height: 6px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {LINE}; min-width: 24px; }}
QScrollBar::handle:horizontal:hover {{ background: {ACCENT}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: {BG}; }}
QDialog {{ background: {BG}; border: 1px solid {ACCENT}; }}
QTextEdit {{ background: {PANEL}; border: 1px solid {LINE}; padding: 6px 8px; selection-background-color: {ACCENT}; }}
QTextEdit:focus {{ border-color: {ACCENT}; }}
QComboBox {{ background: {PANEL}; border: 1px solid {LINE}; padding: 4px 8px; }}
QComboBox:hover, QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {LINE}; selection-background-color: {ACCENT}; selection-color: {BG}; outline: 0; }}
#drawer {{ background: {PANEL}; border-left: 1px solid {LINE}; }}
#drawer QWidget {{ background: {PANEL}; }}
#drawer QLineEdit, #drawer QTextEdit {{ background: {BG}; }}
#drawer QPushButton {{ background: {PANEL}; }}
#drawer QPushButton:checked {{ background: {ACCENT}; }}
#drawer #dim {{ color: {DIM}; }}
#drawer #drawer_title {{ font-size: 16px; font-weight: bold; }}
#drawer QPushButton#collectionbtn {{ background: {ACCENT}; color: {BG}; font-weight: bold; border-color: {ACCENT}; padding: 8px 10px; }}
#drawer QPushButton#collectionbtn:hover {{ background: {TEXT}; border-color: {TEXT}; }}
QTabWidget::pane {{ border: 1px solid {LINE}; top: -1px; }}
QTabBar::tab {{ background: {BG}; color: {DIM}; padding: 7px 16px; border: 1px solid {LINE}; border-bottom: none; margin-right: 2px; }}
QTabBar::tab:selected {{ color: {TEXT}; border-color: {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QSpinBox {{ background: {PANEL}; border: 1px solid {LINE}; padding: 4px 6px; }}
QSpinBox:focus {{ border-color: {ACCENT}; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {LINE}; background: {BG}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QSlider::groove:horizontal {{ height: 4px; background: {LINE}; }}
QSlider::handle:horizontal {{ background: {ACCENT}; width: 12px; margin: -5px 0; }}
QLabel#ok {{ color: #3fb950; }}
QLabel#bad {{ color: {ACCENT}; }}
QLabel#wizlogo {{ color: {ACCENT}; font-size: 22px; font-weight: bold; letter-spacing: 3px; }}
QLabel#wizhead {{ font-size: 16px; font-weight: bold; }}
QLabel#wizstep {{ color: {DIM}; letter-spacing: 2px; }}
QPushButton#primary {{ background: {ACCENT}; color: {BG}; font-weight: bold; border-color: {ACCENT}; padding: 6px 18px; }}
QPushButton#primary:hover {{ background: {TEXT}; border-color: {TEXT}; }}
QPushButton#shelftoggle {{ border: none; padding: 2px 4px; color: {DIM}; text-align: left; }}
QPushButton#shelftoggle:hover {{ color: {TEXT}; }}
QPushButton#shelftoggle:checked {{ background: {BG}; color: {DIM}; font-weight: normal; }}
#drawer QPushButton#statsbtn {{ border: none; text-align: left; padding: 2px 0; color: {TEXT}; background: {PANEL}; }}
#drawer QPushButton#statsbtn:hover {{ color: {ACCENT}; }}
#drawer QPushButton#statsbtn:checked {{ background: {PANEL}; color: {TEXT}; font-weight: normal; }}
#drawer QPushButton#statsbtn:disabled {{ color: {TEXT}; }}
#drawer #sessionlist {{ color: {DIM}; padding-left: 8px; }}
#drawer #synopsis {{ color: {DIM}; font-size: 11px; }}
#drawer #hltbstat {{ color: {TEXT}; }}
#drawer #syncbadge {{ background: {BG}; color: #3fb950; border: 1px solid #2ea043; padding: 2px 6px; }}
#drawer #racard {{ background: {BG}; border: 1px solid {LINE}; }}
#drawer #racard QWidget {{ background: {BG}; }}
#drawer #racard #raaward {{ color: {GOLD}; font-weight: bold; }}
#drawer #racard QPushButton {{ background: {PANEL}; color: {TEXT}; font-weight: normal; }}
#drawer #racard QPushButton:checked {{ background: {PANEL}; color: {TEXT}; border-color: {ACCENT}; font-weight: normal; }}
#drawer QProgressBar {{ background: {PANEL}; border: 1px solid {LINE}; }}
#drawer QProgressBar::chunk {{ background: {ACCENT}; }}
#drawer QProgressBar[mastered="true"]::chunk {{ background: {GOLD}; }}
#drawer QListWidget#ralist {{ background: {BG}; border: 1px solid {LINE}; }}
#drawer QListWidget#ralist::item {{ padding: 4px 6px; border-bottom: 1px solid {LINE}; }}
#drawer #poster {{ background: {BG}; border: 1px solid {LINE}; }}
#drawer #badge {{ background: {BG}; border: 1px solid {LINE}; padding: 1px 6px; font-weight: bold; }}
QPushButton#tagchip {{ padding: 2px 8px; color: {DIM}; }}
QPushButton#tagchip:hover {{ color: {TEXT}; }}
QPushButton#tagchip:checked {{ background: {ACCENT}; border-color: {ACCENT}; color: {BG}; }}
#drawer QPushButton#tagchip {{ background: {BG}; }}
#drawer QPushButton#tagchip:checked {{ background: {ACCENT}; }}
#drawer QPushButton#tagx {{ background: {BG}; border-left: none; padding: 2px 5px; color: {DIM}; }}
#drawer QPushButton#tagx:hover {{ background: {ACCENT}; color: {BG}; }}
QMenu {{ background: {PANEL}; color: {TEXT}; border: 1px solid {LINE}; padding: 4px; }}
QMenu::item {{ padding: 6px 20px; }}
QMenu::item:selected {{ background: {ACCENT}; color: {BG}; }}
"""


def tab_text(p):
    return "★ Favorites" if p == FAV else engine.tab_label(p)


# ---------------------------------------------------------------- background work

class _Signals(QObject):
    poster = Signal(object, object, int)  # group key, QImage or None (no art), poster width it was scaled for
    dropped = Signal(object)             # group key whose task was skipped as stale: forget it was requested
    scanned = Signal(list)               # warnings
    drawer_poster = Signal(int, object)  # drawer token, QImage or None
    drawer_info = Signal(str, object, object)   # kind ('sync' | 'hltb' | 'ra'), what it was for, result (or JobError)
    drawer_badge = Signal(int, int, object)     # RetroAchievements game id, achievement id, QImage or None
    ra_synced = Signal(int, str)         # games matched by a library sync, error text ('' = fine)
    ra_game = Signal(int)                # RetroAchievements game id whose progress was just refreshed


class _CoverPickerSignals(QObject):
    results = Signal(int, object)        # search token, [{'url', 'thumb'}] or None when SteamGridDB was unreachable
    thumb = Signal(int, int, object)     # search token, index, QImage or None
    saved = Signal(bool)                 # success


THUMB_DIR = Path(os.environ.get("WIREPLAY_CACHE") or Path.home() / ".cache/wireplay") / "thumbs"
THUMB_BUCKETS = (120, 160, 200, 260, 340, 440, 520)    # stored widths: zooming reuses a thumb instead of making one per step
THUMB_MIN_SOURCE = 64 << 10                            # smaller originals decode quickly anyway: no copy


def _decode_scaled(path, size: QSize):
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    src_size = reader.size()
    if src_size.isValid() and (src_size.width() > size.width() * 2 or src_size.height() > size.height() * 2):
        reader.setScaledSize(src_size.scaled(size * 2, Qt.KeepAspectRatio))     # cheap decode of huge art
    img = reader.read()
    return img if img.isNull() else img.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def _thumb_stem(path, st, bucket_w):
    """Cache name (without extension) for `path` at a bucket width. The key includes size and mtime, so changed art
    gets a new thumbnail."""
    return hashlib.sha1(f"{path}|{st.st_mtime_ns}|{st.st_size}|{bucket_w}".encode()).hexdigest()[:28]


def load_scaled(path, size: QSize):
    """Decode an image file no larger than needed, then smooth-scale it to fit `size`. Null QImage on failure.

    Large originals (posters average 1 MB, some 3 MB) are decoded once and a small copy is kept in
    ~/.cache/wireplay/thumbs (JPEG, or PNG when the art has transparency); later launches read that instead.
    Anything wrong with the cache just falls back to decoding the original, so it can only make things faster.
    """
    def fit(img):
        return img if img.size() == size else img.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    try:
        st = os.stat(path)
        bucket = next((w for w in THUMB_BUCKETS if w >= size.width()), None)
        if st.st_size < THUMB_MIN_SOURCE or bucket is None:
            return _decode_scaled(path, size)
        stem = _thumb_stem(path, st, bucket)
        for ext in (".jpg", ".png"):
            thumb = THUMB_DIR / f"{stem}{ext}"
            if thumb.is_file():
                img = QImage(str(thumb))
                if not img.isNull():
                    return img if img.width() <= size.width() and img.height() <= size.height() else fit(img)
        full = _decode_scaled(path, QSize(bucket, int(bucket * 1.5)))
        if full.isNull():
            return full
        try:                                           # atomic: a half-written thumbnail is never seen by another thread
            THUMB_DIR.mkdir(parents=True, exist_ok=True)
            ext = ".png" if full.hasAlphaChannel() else ".jpg"
            tmp = THUMB_DIR / f".{stem}.{threading.get_ident()}.part{ext}"
            out = full if ext == ".png" else full.convertToFormat(QImage.Format_RGB888)
            if out.save(str(tmp), "PNG" if ext == ".png" else "JPEG", 90):
                os.replace(tmp, THUMB_DIR / f"{stem}{ext}")
        except OSError:
            pass
        return fit(full)
    except OSError:
        return _decode_scaled(path, size)


class PosterTask(QRunnable):
    """Find/download art for one group and decode + scale it off the UI thread.

    `epoch` is the window's filter generation when the task was queued; once the user has changed
    tab/filter the card is probably gone, so a stale task skips the (slow) network work.
    """

    def __init__(self, key, rows, poster_w, dpr, signals, epoch, current_epoch):
        super().__init__()
        self.key, self.rows, self.signals = key, rows, signals
        self.poster_w = poster_w
        self.size = QSize(int(poster_w * dpr), int(poster_w * 1.5 * dpr))
        self.epoch, self.current_epoch = epoch, current_epoch

    def run(self):
        if self.epoch != self.current_epoch():
            self.signals.dropped.emit(self.key)
            return
        img = None
        try:
            path = covers.fetch_poster(self.rows)
            if path:
                loaded = load_scaled(path, self.size)
                img = None if loaded.isNull() else loaded
        except Exception:                # a bad image or network hiccup must never kill the pool
            img = None
        self.signals.poster.emit(self.key, img, self.poster_w)


class DrawerPosterTask(QRunnable):
    """Larger poster for the details drawer (same art pipeline as the grid, decoded off the UI thread)."""

    def __init__(self, token, rows, size, signals):
        super().__init__()
        self.token, self.rows, self.size, self.signals = token, rows, size, signals

    def run(self):
        img = None
        try:
            path = covers.fetch_poster(self.rows)
            if path:
                loaded = load_scaled(path, self.size)
                img = None if loaded.isNull() else loaded
        except Exception:
            img = None
        try:
            self.signals.drawer_poster.emit(self.token, img)
        except RuntimeError:             # the drawer (and its signal source) was destroyed while we worked
            pass


class JobError:
    """Result of a DrawerJob that failed (network down, rate limit ...): shown as 'nothing', retried on the next view."""

    def __init__(self, text):
        self.text = text


class DrawerJob(QRunnable):
    """One slow drawer lookup (Syncthing file search, HowLongToBeat, RetroAchievements) off the UI thread.

    `fn()` runs on a pool thread; its result (or a JobError) goes back as drawer_info(kind, key, result), where
    `key` says what it was for so the drawer can drop answers for a game that is no longer shown.
    """

    def __init__(self, signals, kind, key, fn):
        super().__init__()
        self.signals, self.kind, self.key, self.fn = signals, kind, key, fn

    def run(self):
        try:
            result = self.fn()
        except Exception as e:           # NetworkError, a bad answer, a missing drive ...: never kill the pool
            result = JobError(f"{type(e).__name__}: {e}")
        try:
            self.signals.drawer_info.emit(self.kind, self.key, result)
        except RuntimeError:             # the drawer was destroyed while we worked
            pass


class BadgeJob(QRunnable):
    """Download (if not cached) and decode achievement badge icons one by one, newest request first dropping stale ones."""

    def __init__(self, signals, ra_id, items, size, still_wanted):
        super().__init__()
        self.signals, self.ra_id, self.items, self.size, self.still_wanted = signals, ra_id, items, size, still_wanted

    def run(self):
        for ach_id, badge, locked in self.items:
            if not self.still_wanted(self.ra_id):
                return
            img = None
            try:
                path = ra_client.fetch_badge(badge, locked)
                if path:
                    loaded = load_scaled(path, self.size)
                    img = None if loaded.isNull() else loaded
            except Exception:
                img = None
            try:
                self.signals.drawer_badge.emit(self.ra_id, ach_id, img)
            except RuntimeError:
                return


class RaSyncTask(QRunnable):
    """Match the library to RetroAchievements and cache X/Y per game (a handful of API calls, cached for hours)."""

    def __init__(self, signals):
        super().__init__()
        self.signals = signals

    def run(self):
        count, error = 0, ""
        try:
            count = ra_client.sync_library()
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
        try:
            self.signals.ra_synced.emit(count, error)
        except RuntimeError:
            pass


class RaIdentifyTask(QRunnable):
    """Hash one ROM (on demand, e.g. when it is launched) and settle its RetroAchievements set by the hash."""

    def __init__(self, signals, game_id):
        super().__init__()
        self.signals, self.game_id = signals, game_id

    def run(self):
        try:
            result = ra_client.identify_row(self.game_id)
        except Exception:
            return                       # offline / drive gone: decided next time
        if result and result["changed"]:
            try:
                self.signals.ra_game.emit(result["ra_game_id"] or 0)
            except RuntimeError:
                pass


class RaGameTask(QRunnable):
    """Refresh one game's achievement progress (after a play session ends)."""

    def __init__(self, signals, ra_id):
        super().__init__()
        self.signals, self.ra_id = signals, ra_id

    def run(self):
        try:
            ra_client.refresh_game(self.ra_id)
        except Exception:
            return                       # offline: the numbers refresh at the next sync
        try:
            self.signals.ra_game.emit(self.ra_id)
        except RuntimeError:
            pass


class SessionWatcher(QObject):
    """Polls running play sessions (see engine.PopenSession / engine.EnvSession) from one timer.

    Signals (`sid` is the sessions-table id, 0 when the DB row could not be created):
      finished(sid, game id, minutes, end epoch)  a session worth recording is over
      ended(game id)                              every session that is over, including discarded ones, so the
                                                  Discord status is always cleared
      discarded(sid)                              a launch that failed at once: close its row, credit nothing
      beat(sid, pid, pid_start, now epoch)        heartbeat every BEAT_SECS (and when the followed pid becomes
                                                  known), so a restarted wireplay can resume or settle the session
    """
    finished = Signal(int, int, int, int)
    ended = Signal(int)
    discarded = Signal(int)
    beat = Signal(int, int, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.active = []
        self.timer = QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self._poll)

    def watch(self, proc, row, session_id=0):
        self.adopt(engine.make_session(proc, row), session_id, time.time())

    def adopt(self, tracker, session_id=0, start_wall=None):
        """Start polling a tracker (a new launch, or one resumed after a restart)."""
        tracker.session_id = session_id or tracker.session_id
        if start_wall is not None:
            tracker.start_wall = start_wall
        tracker.beat_root = tracker.root_info()
        tracker.last_beat = time.monotonic()
        self.active.append(tracker)
        self.timer.start()

    def _poll(self):
        now = time.monotonic()
        taken = set().union(*(sess.pids() for sess in self.active)) if self.active else set()
        still = []
        for sess in self.active:
            try:
                minutes = sess.poll(now, taken)
            except Exception:                              # a tracking bug must never take the launcher down
                minutes = -1
            if minutes is None:
                still.append(sess)
                root = sess.root_info()
                if sess.session_id and (root != sess.beat_root or now - sess.last_beat >= engine.BEAT_SECS):
                    sess.beat_root, sess.last_beat = root, now
                    self.beat.emit(sess.session_id, root[0] if root else 0, root[1] if root else 0, int(time.time()))
                continue
            if minutes >= 0:
                end = sess.start_wall + ((sess.end if sess.end is not None else now) - sess.start)
                self.finished.emit(sess.session_id, sess.game_id, minutes, int(end))
            elif sess.session_id:
                self.discarded.emit(sess.session_id)
            self.ended.emit(sess.game_id)
        self.active = still
        if not still:
            self.timer.stop()


class ScanTask(QRunnable):
    def __init__(self, signals):
        super().__init__()
        self.signals = signals

    def run(self):
        try:
            with db.session() as con:
                warnings = engine.scan(con)
        except Exception as e:
            warnings = [f"scan failed: {e}"]
        self.signals.scanned.emit(warnings)


class FetchAlternativesTask(QRunnable):
    def __init__(self, token, name, row, signals):
        super().__init__()
        self.token, self.name, self.row, self.signals = token, name, row, signals

    def run(self):
        try:
            res = covers.sgdb_fetch_alternatives(self.name, self.row, limit=18)
        except Exception:                # NetworkError (offline, rate-limited, bad key) or anything unexpected
            res = None
        self.signals.results.emit(self.token, res)


class FetchThumbTask(QRunnable):
    def __init__(self, token, idx, url, signals):
        super().__init__()
        self.token, self.idx, self.url, self.signals = token, idx, url, signals

    def run(self):
        img = None
        try:
            got = covers.download_image(self.url)
            if got:
                src = QImage.fromData(got[0])
                if not src.isNull():
                    img = src.scaled(120, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        except Exception:
            img = None
        self.signals.thumb.emit(self.token, self.idx, img)


class SaveCoverTask(QRunnable):
    def __init__(self, rows, url, signals):
        super().__init__()
        self.rows, self.url, self.signals = rows, url, signals

    def run(self):
        try:
            ok = bool(covers.save_custom_cover(self.rows, self.url))
        except Exception:
            ok = False
        self.signals.saved.emit(ok)


# ---------------------------------------------------------------- model / delegate

class GroupModel(QAbstractListModel):
    def __init__(self):
        super().__init__()
        self.groups = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.groups)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        g = self.groups[index.row()]
        if role == Qt.DisplayRole:
            return g["text"]
        if role == GROUP_ROLE:
            return g
        return None

    def set_groups(self, groups):
        self.beginResetModel()
        self.groups = groups
        self.endResetModel()


MISSING = object()                       # PosterCache.get() result for "never looked up" (None means "no art")


class PosterCache:
    """group key -> QPixmap (or None = no art), bounded by decoded size so a 5,000-game library stays small."""

    ENTRY_OVERHEAD = 512                 # bytes charged to every entry, so "no art" markers are not free

    def __init__(self, max_bytes=96 << 20):
        self.max_bytes, self.bytes = max_bytes, 0
        self.items = OrderedDict()       # key -> (pixmap or None, cost)
        self.requested = set()           # keys with a task queued or running (or already answered)
        self.widths = {}                 # key -> poster width the pixmap was scaled for
        self.refreshing = set()          # keys being re-rendered at a new zoom (the old pixmap is still drawn)

    @staticmethod
    def _cost(pix):
        return PosterCache.ENTRY_OVERHEAD + (pix.width() * pix.height() * 4 if pix is not None else 0)

    def get(self, key):
        item = self.items.get(key)
        if item is None:
            return MISSING
        self.items.move_to_end(key)
        return item[0]

    def put(self, key, pix, poster_w=0):
        self.discard(key)
        cost = self._cost(pix)
        self.items[key] = (pix, cost)
        self.widths[key] = poster_w
        self.bytes += cost
        self.requested.add(key)
        while self.bytes > self.max_bytes and len(self.items) > 1:
            old, (_, old_cost) = self.items.popitem(last=False)
            self.bytes -= old_cost
            self.widths.pop(old, None)
            self.requested.discard(old)  # off-screen and evicted: a later paint may ask for it again

    def discard(self, key):
        """Forget a key entirely (cover changed, or its task was skipped)."""
        item = self.items.pop(key, None)
        if item:
            self.bytes -= item[1]
        self.widths.pop(key, None)
        self.refreshing.discard(key)
        self.requested.discard(key)


class CardDelegate(QStyledItemDelegate):
    def __init__(self, view, cache, pool, signals, epoch):
        super().__init__(view)
        self.view, self.cache, self.pool, self.signals, self.epoch = view, cache, pool, signals, epoch
        self.small = QFont(view.font())
        self.small.setPointSizeF(max(7.0, view.font().pointSizeF() - 2.5))
        self.small.setBold(True)
        self.badge_fm = QFontMetrics(self.small)
        self.title_fm = QFontMetrics(view.font())
        self.settled = True              # False while the user is still zooming: old pixmaps are scaled, not re-rendered
        self.set_poster_w(DEFAULT_POSTER_W)

    def set_poster_w(self, w):
        self.poster_w, self.poster_h, self.card_w, self.card_h = card_metrics(w)
        self.settled = False

    def sizeHint(self, option, index):
        return QSize(self.card_w, self.card_h)

    def paint(self, p, option, index):
        g = index.data(GROUP_ROLE)
        if not g:
            return
        key, is_installed = g["key"], g["installed"]
        dpr = self.view.devicePixelRatioF()
        rect = option.rect.adjusted(4, 4, -4, -4)
        hover = bool(option.state & QStyle.State_MouseOver)
        selected = bool(option.state & QStyle.State_Selected)

        p.save()
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(rect, QColor(PANEL))
        p.setPen(QPen(QColor(ACCENT if hover or selected else LINE), 1))
        p.drawRect(rect.adjusted(0, 0, -1, -1))

        # poster area (2:3), art centred inside it keeping its own aspect ratio
        pw, ph = self.poster_w, self.poster_h
        area = QRect(rect.x() + 7, rect.y() + 7, pw, ph)
        p.fillRect(area, QColor(BG))

        if not is_installed:
            p.setOpacity(0.55)

        pix = self.cache.get(key)
        if pix is MISSING:
            if key not in self.cache.requested:
                self.cache.requested.add(key)
                self.pool.start(PosterTask(key, g["rows"], pw, dpr, self.signals, self.epoch[0], lambda: self.epoch[0]))
            self._placeholder(p, area, g, "…")
        elif pix is None:
            self._placeholder(p, area, g, "no cover")
        else:
            w, h = pix.width() / pix.devicePixelRatio(), pix.height() / pix.devicePixelRatio()
            rendered_for = self.cache.widths.get(key) or pw
            if rendered_for != pw:                       # zoom changed: show the old render scaled until a new one arrives
                w, h = w * pw / rendered_for, h * pw / rendered_for
                if self.settled and key not in self.cache.refreshing:
                    self.cache.refreshing.add(key)
                    self.pool.start(PosterTask(key, g["rows"], pw, dpr, self.signals, self.epoch[0],
                                               lambda: self.epoch[0]))
            p.drawPixmap(QRectF(area.x() + (pw - w) / 2, area.y() + (ph - h) / 2, w, h), pix, QRectF(pix.rect()))

        p.setOpacity(1.0)

        # badges
        p.setFont(self.small)
        fm = self.badge_fm
        plat = "STEAM" if g["platform"] == "steam" else g["platform"].upper()
        self._badge(p, fm, area.x() + 4, area.y() + 4, plat, QColor(BG), QColor(TEXT), QColor(LINE))
        ra = engine.ra_badge(g)                           # 🏆 X/Y, or a gold 🥇 Mastered
        if ra:
            text, gold = ra
            self._badge(p, fm, area.x() + 4, area.y() + 8 + fm.height(), text,
                        QColor(GOLD) if gold else QColor(BG), QColor(BG) if gold else QColor(GOLD), QColor(GOLD))

        right = area.right() - 3                          # top-right badges grow leftwards from here
        if g["favorite"]:
            bw_star = fm.horizontalAdvance("★") + 12
            right -= bw_star
            self._badge(p, fm, right, area.y() + 4, "★", QColor(BG), QColor(GOLD), QColor(GOLD))
            right -= 4
        if not is_installed:
            bw_inst = fm.horizontalAdvance("INSTALL") + 12
            self._badge(p, fm, right - bw_inst, area.y() + 4, "INSTALL", QColor(ACCENT), QColor(BG), QColor(ACCENT))

        bottom = area.bottom() - fm.height() - 9
        if len(g["rows"]) > 1:
            txt = f"{len(g['rows'])} versions"
            bw = fm.horizontalAdvance(txt) + 12
            self._badge(p, fm, area.right() - bw - 3, bottom, txt, QColor(ACCENT), QColor(BG), QColor(ACCENT))
            bottom -= fm.height() + 8                     # keep clear of the versions badge
        x = area.x() + 4
        if engine.played_recently(g["last_played"]):
            d = engine.days_since(g["last_played"])
            txt = "▶ today" if d <= 0 else f"▶ {d}d"
            self._badge(p, fm, x, bottom, txt, QColor(BG), QColor(ACCENT), QColor(ACCENT))
            x += fm.horizontalAdvance(txt) + 16
        hours = engine.fmt_playtime_badge(g["playtime"])
        if hours:
            self._badge(p, fm, x, bottom, hours, QColor(BG), QColor(TEXT), QColor(LINE))

        # title (two lines max)
        p.setFont(self.view.font())
        p.setPen(QColor(TEXT))
        tfm = self.title_fm
        title = g["text"] if len(g["rows"]) == 1 else g["base"]
        title = tfm.elidedText(title, Qt.ElideRight, int((pw - 4) * 1.85))
        p.drawText(QRect(area.x() + 2, area.bottom() + 6, pw - 4, 2 * tfm.height() + 2),
                   Qt.TextWordWrap | Qt.AlignTop | Qt.AlignLeft, title)
        p.restore()

    def _badge(self, p, fm, x, y, text, bg, fg, border):
        r = QRect(x, y, fm.horizontalAdvance(text) + 12, fm.height() + 4)
        p.fillRect(r, bg)
        p.setPen(QPen(border, 1))
        p.drawRect(r.adjusted(0, 0, -1, -1))
        p.setPen(fg)
        p.drawText(r, Qt.AlignCenter, text)

    def _placeholder(self, p, area, g, note):
        p.setPen(QPen(QColor(LINE), 1))
        p.drawRect(area.adjusted(10, 40, -11, -41))
        p.setPen(QColor(DIM))
        p.drawText(area, Qt.AlignCenter | Qt.TextWordWrap, f"{g['platform']}\n{note}")


class PosterView(QListView):
    activate_group = Signal(dict)
    request_cover = Signal(dict)
    toggle_favorite = Signal(dict)
    toggle_details = Signal()
    show_details = Signal()
    browse_collection = Signal(dict)
    zoom_by = Signal(float)              # card-width change in px (Ctrl + wheel)

    def __init__(self):
        super().__init__()
        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setUniformItemSizes(True)
        self.setSpacing(6)
        self.setLayoutMode(QListView.Batched)
        self.setBatchSize(60)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setMouseTracking(True)
        self.setFrameShape(QFrame.NoFrame)
        self.verticalScrollBar().setSingleStep(40)
        self.clicked.connect(lambda i: self.activate_group.emit(i.data(GROUP_ROLE)))

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self.zoom_by.emit(e.angleDelta().y() / 120 * ZOOM_STEP)    # a notch = one step; smooth wheels give fractions
            e.accept()
        else:
            super().wheelEvent(e)

    def keyPressEvent(self, e):
        cur = self.currentIndex()
        plain = not (e.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier))
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and cur.isValid():
            self.activate_group.emit(cur.data(GROUP_ROLE))
        elif plain and e.key() == Qt.Key_F and cur.isValid():
            self.toggle_favorite.emit(cur.data(GROUP_ROLE))
        elif plain and e.key() in (Qt.Key_Space, Qt.Key_I):
            self.toggle_details.emit()
        else:
            super().keyPressEvent(e)

    def contextMenuEvent(self, e):
        idx = self.indexAt(e.pos())
        g = idx.data(GROUP_ROLE) if idx.isValid() else None
        if not g:
            return
        self.setCurrentIndex(idx)                               # the details drawer follows the right-clicked card
        menu = QMenu(self)                                      # styled by the application QSS
        act_launch = menu.addAction("▶ Launch" if g["installed"] else "⬇ Install (Steam)")
        act_fav = menu.addAction("☆ Remove from Favorites" if g["favorite"] else "★ Add to Favorites")
        act_details = menu.addAction("📋 Show Details")
        act_cover = menu.addAction("🎨 Change Cover Art…")
        big = len(g["rows"]) > COLLECTION_MIN
        act_version = menu.addAction("📋 Select Version…") if 1 < len(g["rows"]) and not big else None
        act_browse = menu.addAction(f"🎮 Browse All Versions… ({len(g['rows'])})") if big else None
        chosen = menu.exec(e.globalPos())
        if chosen is None:
            return
        if chosen == act_cover:
            self.request_cover.emit(g)
        elif chosen == act_fav:
            self.toggle_favorite.emit(g)
        elif chosen == act_details:
            self.show_details.emit()
        elif act_browse is not None and chosen == act_browse:
            self.browse_collection.emit(g)
        elif chosen in (act_launch, act_version):               # both open the version list for stacked games
            self.activate_group.emit(g)


# ---------------------------------------------------------------- dialogs / window

class CoverPickerDialog(QDialog):
    """Pick alternative vertical poster art from SteamGridDB."""

    def __init__(self, group, parent, pool):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.setModal(True)
        self.group = group
        self.rows = covers.representatives(group["rows"])
        self.pool = pool
        self.applied = False
        self.token = 0                                          # identifies the latest search; older replies are ignored
        self.signals = _CoverPickerSignals()
        self.signals.results.connect(self._on_results)
        self.signals.thumb.connect(self._on_thumb)
        self.signals.saved.connect(self._on_saved)

        self.resize(680, 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        # Title bar
        head = QHBoxLayout()
        title = QLabel(f"Cover Art  ·  {group['base']}")
        title.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {TEXT};")
        close_btn = QPushButton("✕")
        close_btn.setObjectName("winbtn")
        close_btn.setFixedSize(28, 28)
        close_btn.clicked.connect(self.reject)
        head.addWidget(title)
        head.addStretch()
        head.addWidget(close_btn)
        lay.addLayout(head)

        # Search row
        srow = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setText(group["base"])
        self.search_box.setPlaceholderText("Search SteamGridDB title…")
        self.search_box.returnPressed.connect(self.search)
        btn_search = QPushButton("Search")
        btn_search.clicked.connect(self.search)
        srow.addWidget(self.search_box, 1)
        srow.addWidget(btn_search)
        lay.addLayout(srow)

        self.status_lbl = QLabel("Searching SteamGridDB for 600×900 vertical posters…")
        self.status_lbl.setStyleSheet(f"color: {DIM};")
        lay.addWidget(self.status_lbl)

        # Cover grid
        self.list = QListWidget()
        self.list.setViewMode(QListView.IconMode)
        self.list.setIconSize(QSize(120, 180))
        self.list.setGridSize(QSize(134, 196))
        self.list.setMovement(QListView.Static)
        self.list.setResizeMode(QListView.Adjust)
        self.list.setSpacing(6)
        self.list.itemSelectionChanged.connect(self._selection_changed)
        self.list.itemDoubleClicked.connect(lambda _i: self.apply_chosen())
        lay.addWidget(self.list, 1)

        # Bottom buttons
        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.apply_btn = QPushButton("Apply Cover")
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self.apply_chosen)
        btns.addWidget(cancel)
        btns.addWidget(self.apply_btn)
        lay.addLayout(btns)

        self.search()

    def search(self):
        query = self.search_box.text().strip()
        if not query:
            return
        self.token += 1
        self.list.clear()
        self.apply_btn.setEnabled(False)
        self.status_lbl.setText(f"Searching SteamGridDB for '{query}'…")
        self.pool.start(FetchAlternativesTask(self.token, query, self.rows[0], self.signals))

    def _on_results(self, token, results):
        if token != self.token:                                 # a newer search is under way
            return
        self.list.clear()
        if results is None:
            self.status_lbl.setText("Could not reach SteamGridDB (offline, rate-limited, or API key missing/rejected).")
            return
        if not results:
            self.status_lbl.setText("No 600×900 posters found on SteamGridDB for this query.")
            return
        self.status_lbl.setText(f"Found {len(results)} posters. Click one to choose, or double-click to apply:")
        for i, item in enumerate(results):
            witem = QListWidgetItem()
            witem.setData(Qt.UserRole, item["url"])
            witem.setSizeHint(QSize(128, 190))
            self.list.addItem(witem)
            self.pool.start(FetchThumbTask(token, i, item["thumb"], self.signals))

    def _on_thumb(self, token, idx, img):
        if token == self.token and 0 <= idx < self.list.count() and img is not None:
            self.list.item(idx).setIcon(QPixmap.fromImage(img))

    def _selection_changed(self):
        self.apply_btn.setEnabled(bool(self.list.selectedItems()))

    def apply_chosen(self):
        items = self.list.selectedItems()
        url = items[0].data(Qt.UserRole) if items else None
        if not url or not self.apply_btn.isEnabled():           # nothing chosen, or a save is already running
            return
        self.apply_btn.setEnabled(False)
        self.status_lbl.setText("Downloading and caching selected cover art…")
        self.pool.start(SaveCoverTask(self.rows, url, self.signals))

    def _on_saved(self, success):
        if success:
            self.applied = True
            self.accept()
        else:
            self.status_lbl.setText("Failed to save cover art.")
            self.apply_btn.setEnabled(bool(self.list.selectedItems()))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.reject()
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self.search_box.hasFocus():
                self.search()
            elif self.list.selectedItems():
                self.apply_chosen()
        else:
            super().keyPressEvent(e)


class FlowLayout(QLayout):
    """Left-to-right layout that wraps onto new lines (the tag chips of the details drawer)."""

    def __init__(self, parent=None, spacing=4):
        super().__init__(parent)
        self._items = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _arrange(self, rect, apply):
        m = self.contentsMargins()
        x0, x, y, line_h = rect.x() + m.left(), rect.x() + m.left(), rect.y() + m.top(), 0
        right = rect.right() - m.right() + 1
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > right and line_h > 0:
                x, y, line_h = x0, y + line_h + self.spacing(), 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self.spacing()
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y() + m.bottom()


class TagChip(QWidget):
    """`#tag` button (click = filter the grid by it) with a small x button (remove from this game)."""
    clicked = Signal(str)
    removed = Signal(str)

    def __init__(self, tag):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.label = QPushButton(f"#{tag}")
        self.label.setObjectName("tagchip")
        self.label.setFocusPolicy(Qt.NoFocus)
        self.label.setToolTip(f"Show only games tagged {tag}")
        self.label.clicked.connect(lambda: self.clicked.emit(tag))
        self.x = QPushButton("×")
        self.x.setObjectName("tagx")
        self.x.setFocusPolicy(Qt.NoFocus)
        self.x.setToolTip(f"Remove tag {tag}")
        self.x.clicked.connect(lambda: self.removed.emit(tag))
        lay.addWidget(self.label)
        lay.addWidget(self.x)


class Drawer(QFrame):
    """Collapsible right-hand details panel for the selected game (poster, stats, favorite, folder, args, notes).

    Notes and launch arguments belong to one DB row. A stacked game has one row per version, so a combo box
    picks the version when there is more than one. Edits are written to the DB debounced, and always flushed
    before the shown game changes or the window closes.
    """
    favorite_clicked = Signal(dict)
    tags_changed = Signal()
    tag_clicked = Signal(str)                            # tag key to filter the grid by
    ra_updated = Signal()                                # fresh RetroAchievements numbers were stored: refresh the cards
    browse_requested = Signal(dict)                      # open the collection browser for this stack
    status = Signal(str)
    RA_REFRESH_SECS = 120.0                              # how long a fetched achievement list is reused
    SESSIONS_SHOWN = 8                                   # recent play sessions listed under the playtime

    def __init__(self, pool):
        super().__init__()
        self.setObjectName("drawer")
        self.pool, self.signals = pool, _Signals(self)   # parented: lives and dies with the drawer
        self.signals.drawer_poster.connect(self._poster_ready)
        self.signals.drawer_info.connect(self._info_ready)
        self.signals.drawer_badge.connect(self._badge_ready)
        self.row, self.ra_id, self.ra_detail, self.ra_items = None, None, None, {}
        self.ra_memo = {}                                # ra game id -> (monotonic time, detail)
        self.pending_hltb = set()                        # (platform, base title) lookups running right now
        self.sync_folder = None
        self.group, self.row_id, self.token = None, None, 0
        self.notes_dirty = self.args_dirty = False
        self.folder = None
        self.tags = []
        self.setMinimumWidth(0)                          # fixed width would forbid animating maximumWidth to 0
        self.setMaximumWidth(DRAWER_W)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()                           # short windows scroll instead of squashing the notes
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        body = QWidget()
        body.setMinimumWidth(DRAWER_W)                   # content is clipped, not squeezed, while sliding
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)

        self.poster = QLabel("select a game")
        self.poster.setObjectName("poster")
        self.poster.setAlignment(Qt.AlignCenter)
        self.poster.setFixedSize(200, 300)
        lay.addWidget(self.poster, 0, Qt.AlignHCenter)

        # Header: title, platform, release year, install state
        self.title = QLabel("")
        self.title.setObjectName("drawer_title")
        self.title.setWordWrap(True)
        lay.addWidget(self.title)

        badges = QHBoxLayout()
        self.platform = QLabel("")
        self.platform.setObjectName("badge")
        self.year = QLabel("")
        self.year.setObjectName("badge")
        self.year.hide()
        self.state = QLabel("")
        self.state.setObjectName("dim")
        badges.addWidget(self.platform)
        badges.addWidget(self.year)
        badges.addWidget(self.state)
        badges.addStretch()
        lay.addLayout(badges)

        self.btn_collection = QPushButton("")              # stacks of more than COLLECTION_MIN versions
        self.btn_collection.setObjectName("collectionbtn")
        self.btn_collection.setFocusPolicy(Qt.NoFocus)
        self.btn_collection.clicked.connect(lambda: self.group and self.browse_requested.emit(self.group))
        self.btn_collection.hide()
        lay.addWidget(self.btn_collection)
        self.versions = QComboBox()
        self.versions.currentIndexChanged.connect(self._version_changed)
        lay.addWidget(self.versions)

        # Synopsis (muted, two sentences)
        self.synopsis = QLabel("")
        self.synopsis.setObjectName("synopsis")
        self.synopsis.setWordWrap(True)
        self.synopsis.hide()
        lay.addWidget(self.synopsis)

        # HowLongToBeat
        self.hltb_box = QWidget()
        hv = QVBoxLayout(self.hltb_box)
        hv.setContentsMargins(0, 0, 0, 0)
        hv.setSpacing(4)
        hv.addWidget(self._caption("HowLongToBeat"))
        stats_host = QWidget()
        self.hltb_flow = FlowLayout(stats_host, spacing=12)
        self.hltb_labels = {}
        for name in ("main", "extra", "complete"):
            label = QLabel("")
            label.setObjectName("hltbstat")
            self.hltb_labels[name] = label
            self.hltb_flow.addWidget(label)
        hv.addWidget(stats_host)
        self.hltb_note = QLabel("")
        self.hltb_note.setObjectName("dim")
        hv.addWidget(self.hltb_note)
        self.hltb_box.hide()
        lay.addWidget(self.hltb_box)

        # RetroAchievements card
        self.ra_box = QFrame()
        self.ra_box.setObjectName("racard")
        rv = QVBoxLayout(self.ra_box)
        rv.setContentsMargins(10, 8, 10, 8)
        rv.setSpacing(6)
        head = QHBoxLayout()
        head.addWidget(self._caption("RetroAchievements"))
        head.addStretch()
        self.ra_award = QLabel("")
        self.ra_award.setObjectName("raaward")
        head.addWidget(self.ra_award)
        rv.addLayout(head)
        self.ra_bar = QProgressBar()
        self.ra_bar.setTextVisible(False)
        self.ra_bar.setFixedHeight(10)
        rv.addWidget(self.ra_bar)
        self.ra_summary = QLabel("")
        self.ra_summary.setWordWrap(True)
        rv.addWidget(self.ra_summary)
        self.ra_verified = QLabel("✓ this exact ROM is supported (matched by its hash)")
        self.ra_verified.setObjectName("dim")
        self.ra_verified.setWordWrap(True)
        self.ra_verified.hide()
        rv.addWidget(self.ra_verified)
        self.ra_toggle = QPushButton("")
        self.ra_toggle.setFocusPolicy(Qt.NoFocus)
        self.ra_toggle.setCheckable(True)
        self.ra_toggle.toggled.connect(self._ra_toggled)
        rv.addWidget(self.ra_toggle)
        self.ra_list = QListWidget()
        self.ra_list.setObjectName("ralist")
        self.ra_list.setIconSize(QSize(40, 40))
        self.ra_list.setWordWrap(True)
        self.ra_list.setTextElideMode(Qt.ElideNone)          # long descriptions wrap instead of being cut off
        self.ra_list.setSelectionMode(QAbstractItemView.NoSelection)
        self.ra_list.setFocusPolicy(Qt.NoFocus)
        self.ra_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.ra_list.hide()
        rv.addWidget(self.ra_list)
        self.ra_box.hide()
        lay.addWidget(self.ra_box)

        # Save sync & playtime
        lay.addWidget(self._caption("Save sync & playtime"))
        badge_row = QHBoxLayout()                          # the badge keeps its full text; the button sits below it
        self.sync_badge = QLabel("")
        self.sync_badge.setObjectName("syncbadge")
        self.sync_badge.hide()
        badge_row.addWidget(self.sync_badge)
        badge_row.addStretch()
        lay.addLayout(badge_row)
        self.btn_sync = QPushButton("📂 Synced Save")
        self.btn_sync.setFocusPolicy(Qt.NoFocus)
        self.btn_sync.clicked.connect(self._open_synced_save)
        self.btn_sync.hide()
        lay.addWidget(self.btn_sync)
        self.stats = QPushButton("")                       # "Playtime: 3h 5m | Last played: today  ▸": click for sessions
        self.stats.setObjectName("statsbtn")
        self.stats.setFlat(True)
        self.stats.setCheckable(True)
        self.stats.setFocusPolicy(Qt.NoFocus)
        self.stats.setCursor(Qt.PointingHandCursor)
        self.stats.toggled.connect(self._sessions_toggled)
        lay.addWidget(self.stats)
        self.session_list = QLabel("")
        self.session_list.setObjectName("sessionlist")
        self.session_list.setWordWrap(True)
        self.session_list.hide()
        lay.addWidget(self.session_list)
        self.session_count = 0

        # Actions
        lay.addWidget(self._caption("Actions"))
        buttons = QHBoxLayout()
        self.btn_fav = QPushButton("★ Favorite")
        self.btn_fav.setCheckable(True)
        self.btn_fav.setFocusPolicy(Qt.NoFocus)
        self.btn_fav.clicked.connect(lambda: self.group and self.favorite_clicked.emit(self.group))
        self.btn_folder = QPushButton("📁 Open Folder")
        self.btn_folder.setFocusPolicy(Qt.NoFocus)
        self.btn_folder.clicked.connect(self._open_folder)
        buttons.addWidget(self.btn_fav)
        buttons.addWidget(self.btn_folder)
        lay.addLayout(buttons)

        lay.addWidget(self._caption("Tags"))
        self.tag_box = QWidget()
        self.tag_flow = FlowLayout(self.tag_box)
        lay.addWidget(self.tag_box)
        self.tag_input = QLineEdit()
        self.tag_input.setPlaceholderText("add tag, press Enter")
        self.tag_completer = QCompleter([], self.tag_input)
        self.tag_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.tag_completer.setFilterMode(Qt.MatchContains)
        self.tag_input.setCompleter(self.tag_completer)
        self.tag_input.returnPressed.connect(self._add_tags)
        lay.addWidget(self.tag_input)

        lay.addWidget(self._caption("Custom launch arguments"))
        self.args = QLineEdit()
        self.args.setPlaceholderText("extra flags, e.g. -windowed")
        self.args.textEdited.connect(lambda _t: setattr(self, "args_dirty", True))
        self.args.editingFinished.connect(self.flush)
        lay.addWidget(self.args)

        lay.addWidget(self._caption("Personal notes"))
        self.notes = QTextEdit()
        self.notes.setAcceptRichText(False)
        self.notes.setMinimumHeight(110)
        self.notes.setPlaceholderText("cheats, walkthrough links, keybindings…")
        self.notes.textChanged.connect(self._notes_edited)
        lay.addWidget(self.notes, 1)

        self.save_timer = QTimer(self)                   # autosave 700 ms after the last keystroke
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(700)
        self.save_timer.timeout.connect(self.flush)
        self._set_enabled(False)

    def sizeHint(self):
        return QSize(DRAWER_W, super().sizeHint().height())   # the layout grows it up to the animated maximumWidth

    @staticmethod
    def _caption(text):
        label = QLabel(text.upper())
        label.setObjectName("dim")
        return label

    def _set_enabled(self, on):
        for w in (self.btn_fav, self.btn_folder, self.args, self.notes, self.versions, self.tag_input):
            w.setEnabled(on)

    # -- tags
    def set_known_tags(self, names):
        """Tags that exist in the library: offered as completions while typing."""
        self.tag_completer.model().setStringList(list(names))

    def _show_tags(self, tags):
        self.tags = list(tags)
        while self.tag_flow.count():
            item = self.tag_flow.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        for tag in self.tags:
            chip = TagChip(tag)
            chip.clicked.connect(lambda t: self.tag_clicked.emit(engine.tag_key(t)))
            chip.removed.connect(self._remove_tag)
            self.tag_flow.addWidget(chip)
        self.tag_box.setVisible(bool(self.tags))
        self.tag_box.updateGeometry()

    def _save_tags(self, tags):
        """Write the tags of the whole stacked group, show them, and let the window refresh its collection chips."""
        if self.group is None:
            return
        try:
            with db.session() as con:
                saved = db.update_group_tags(con, self.group["base"], self.group["platform"], tags)
        except Exception as e:                           # a locked/broken DB must not take the UI down
            self.status.emit(f"Could not save tags: {e}")
            return
        self._show_tags(saved)
        self.tags_changed.emit()

    def _add_tags(self):
        text = self.tag_input.text()
        self.tag_input.clear()
        if text.strip():
            self._save_tags([*self.tags, *db.parse_tags(text)])

    def _remove_tag(self, tag):
        self._save_tags([t for t in self.tags if t != tag])

    # -- persistence
    def flush(self):
        """Write pending notes / arguments for the row being shown."""
        self.save_timer.stop()
        if self.row_id is None or not (self.notes_dirty or self.args_dirty):
            return
        try:
            with db.session() as con:
                if self.notes_dirty:
                    db.update_game_notes(con, self.row_id, self.notes.toPlainText())
                if self.args_dirty:
                    db.update_custom_args(con, self.row_id, self.args.text())
        except Exception as e:                           # a locked/broken DB must not take the UI down
            self.status.emit(f"Could not save notes: {e}")
            return
        self.notes_dirty = self.args_dirty = False

    def _notes_edited(self):
        if self.row_id is not None:
            self.notes_dirty = True
            self.save_timer.start()

    # -- showing a game
    def set_group(self, group):
        """Show a group (None clears the drawer)."""
        self.flush()
        if (group is not None and self.group is not None and group["key"] == self.group["key"]
                and len(group["rows"]) == len(self.group["rows"]) and self.row_id is not None):
            self.group = group                           # same game after a reload (favorite, tags, session): no re-render
            self._refresh_light(group)
            return
        self.group = group
        self.versions.blockSignals(True)
        self.versions.clear()
        if group is None:
            self.row_id = None
            self.versions.blockSignals(False)
            self._clear()
            return
        rows = covers.representatives(group["rows"])
        for r, label in ((r, group["labels"][group["rows"].index(r)]) for r in rows):
            self.versions.addItem(label, r["id"])
        self.versions.setVisible(len(rows) > 1)
        self.versions.blockSignals(False)
        big = len(rows) > COLLECTION_MIN
        self.btn_collection.setText(f"🎮 Browse Collection ({len(rows)})")
        self.btn_collection.setVisible(big)
        self.title.setText(group["base"])
        self.platform.setText(tab_text(group["platform"]).upper())
        self.state.setText("" if group["installed"] else "not installed")
        self.btn_fav.setChecked(group["favorite"])
        self.btn_fav.setText("★ Favorite" if group["favorite"] else "☆ Favorite")
        self._show_tags(group["tags"])
        self._set_poster(rows)
        self._load_row(rows[0]["id"])

    def _refresh_light(self, group):
        """Update what a reload can change (favorite, tags, state, stats, cached numbers) without touching the
        poster, notes or arguments, and without starting any lookup."""
        self.state.setText("" if group["installed"] else "not installed")
        self.btn_fav.setChecked(group["favorite"])
        self.btn_fav.setText("★ Favorite" if group["favorite"] else "☆ Favorite")
        if group["tags"] != self.tags:
            self._show_tags(group["tags"])
        try:
            with db.session() as con:
                row = db.get_game(con, self.row_id)
        except Exception:
            return
        if row is not None:
            self._show_row_info(row)

    def _clear(self):
        self.title.setText("")
        self.platform.setText("")
        self.state.setText("")
        self.stats.setText("")
        self.stats.setChecked(False)
        self.session_list.hide()
        self._show_tags([])
        self.row, self.ra_id, self.ra_detail = None, None, None
        for w in (self.year, self.synopsis, self.hltb_box, self.ra_box, self.sync_badge, self.btn_sync):
            w.hide()
        self.poster.setPixmap(QPixmap())
        self.poster.setText("select a game")
        self.versions.setVisible(False)
        self.btn_collection.hide()
        self._set_enabled(False)
        for w in (self.args, self.notes):
            w.blockSignals(True)
            w.clear()
            w.blockSignals(False)

    def select_version(self, row_id):
        """Show one version of the current stack (used by the collection browser's 'Show in details')."""
        i = self.versions.findData(row_id)
        if i >= 0:
            self.versions.setCurrentIndex(i)

    def _version_changed(self, index):
        if index >= 0:
            self.flush()
            self._load_row(self.versions.itemData(index))

    def _load_row(self, row_id):
        """Fill the per-row widgets from the DB (not from the group, whose rows can be stale), then start the
        background lookups (Syncthing, HowLongToBeat, RetroAchievements) that this row still needs."""
        with db.session() as con:
            row = db.get_game(con, row_id)
        if row is None:
            return
        self.row_id = row["id"]
        self._set_enabled(True)
        for w, text in ((self.args, row["custom_args"]), (self.notes, row["notes"])):
            w.blockSignals(True)
            if isinstance(w, QTextEdit):
                w.setPlainText(text)
            else:
                w.setText(text)
            w.blockSignals(False)
        self.notes_dirty = self.args_dirty = False
        self.folder = engine.game_folder(row)
        self.btn_folder.setEnabled(self.folder is not None)
        self.btn_folder.setToolTip(str(self.folder) if self.folder else "no folder known (not installed?)")
        self.sync_badge.hide()
        self.btn_sync.hide()
        self.stats.setChecked(False)                     # …and the session list
        self.ra_toggle.setChecked(False)                 # a new game starts with the achievement list collapsed
        self.ra_list.clear()
        self.ra_items = {}
        self.ra_detail = None
        self._show_row_info(row)
        self._start_lookups(row)

    # -- synopsis / HowLongToBeat / RetroAchievements / Syncthing
    def _show_row_info(self, row):
        """Everything the DB already knows about this row: year, synopsis, times, achievement numbers, playtime."""
        self.row = dict(row)
        year = row["release_year"] or ""
        self.year.setText(year)
        self.year.setVisible(bool(year))
        self._show_synopsis(row["synopsis"] or "")
        self._show_hltb(row["hltb_main"], row["hltb_extra"], row["hltb_complete"])
        self._show_ra_summary(row)
        self._show_stats(row)

    def _show_stats(self, row):
        """Playtime line (a button when there are sessions to list) and, if expanded, the recent sessions."""
        try:
            with db.session() as con:
                sessions = db.session_history(con, row["id"], self.SESSIONS_SHOWN)
        except Exception:
            sessions = []
        self.session_count = len(sessions)
        arrow = ("▾" if self.stats.isChecked() else "▸") if sessions else ""
        # two lines: a button does not wrap, and "Last played: yesterday" next to the playtime is wider than the drawer
        self.stats.setText(f"Playtime: {engine.fmt_playtime(row['total_playtime_minutes'])}  {arrow}".rstrip()
                           + f"\nLast played: {engine.fmt_last_played(row['last_played'])}")
        self.stats.setEnabled(bool(sessions) or self.stats.isChecked())
        self.stats.setToolTip("Click for recent play sessions" if sessions else "No sessions recorded yet")
        self.session_list.setText("\n".join(engine.fmt_session_line(s["start_time"], s["duration_minutes"]) for s in sessions)
                                  or "No sessions recorded yet")
        self.session_list.setVisible(self.stats.isChecked() and bool(sessions))

    def _sessions_toggled(self, on):
        if self.row is not None:
            self._show_stats(self.row)

    def _show_synopsis(self, text):
        self.synopsis.setText(text)
        self.synopsis.setVisible(bool(text))

    def _hltb_key(self, row):
        """What a HowLongToBeat lookup is for: the row's own title (not the stack's base title)."""
        return (row["platform"], metadata.clean_title(row["title"]).lower())

    def _hltb_pending(self):
        return bool(self.row) and self._hltb_key(self.row) in self.pending_hltb

    def _show_hltb(self, main, extra, complete):
        """⏱️ Main ~Xh | 🗡️ Extra ~Yh | 🏆 100% ~Zh, hidden when HowLongToBeat has nothing (or while nothing is known)."""
        texts = {"main": ("⏱️ Main", main), "extra": ("🗡️ Extra", extra), "complete": ("🏆 100%", complete)}
        any_value = False
        for name, (label, hours_) in texts.items():
            shown = metadata.fmt_hours(hours_)
            self.hltb_labels[name].setText(f"{label}: {shown}" if shown else "")
            self.hltb_labels[name].setVisible(bool(shown))
            any_value = any_value or bool(shown)
        loading = self._hltb_pending() and not any_value
        self.hltb_note.setText("looking up…" if loading else "")
        self.hltb_note.setVisible(loading)
        self.hltb_box.setVisible(any_value or loading)
        self.hltb_box.updateGeometry()

    def _show_ra_summary(self, row):
        """The RetroAchievements card from cached DB numbers (live numbers replace them when the fetch returns)."""
        total = row["ra_total"] or 0
        if not (ra_client.enabled() and row["ra_game_id"] and total > 0):
            self.ra_id = None
            self.ra_box.hide()
            return
        self.ra_id = row["ra_game_id"]
        self._set_ra_numbers(total, row["ra_unlocked"] or 0, row["ra_points"] or 0)
        self.ra_verified.setVisible(row["ra_verified"] == 1)
        self.ra_box.show()

    def _set_ra_numbers(self, total, unlocked, points):
        mastered = total > 0 and unlocked >= total
        self.ra_bar.setRange(0, max(1, total))
        self.ra_bar.setValue(min(unlocked, total))
        self.ra_bar.setProperty("mastered", mastered)
        self.ra_bar.style().unpolish(self.ra_bar)
        self.ra_bar.style().polish(self.ra_bar)
        self.ra_award.setText("🥇 Mastered" if mastered else "")
        self.ra_summary.setText(f"{unlocked}/{total} achievements  ·  {points} pts (hardcore)")
        self.ra_toggle.setText(f"{'▾' if self.ra_toggle.isChecked() else '▸'} Achievements ({total})")

    def _start_lookups(self, row):
        """Queue what the DB cannot answer yet. Each lookup is a pool job; nothing here waits on I/O."""
        r = dict(row)
        if db.source_of(r["platform"]) == "roms":
            self.pool.start(DrawerJob(self.signals, "sync", r["id"], lambda: engine.find_synced_save(r)))
        key = self._hltb_key(r)
        if db.metadata_missing(row) and key not in self.pending_hltb:
            self.pending_hltb.add(key)
            self._show_hltb(row["hltb_main"], row["hltb_extra"], row["hltb_complete"])      # shows "looking up…"
            self.pool.start(DrawerJob(self.signals, "hltb", key, lambda: metadata.fetch_row(r["id"])))
        if ra_client.needs_identify(row):                  # hash the ROM (on demand) and settle its set exactly
            rid = r["id"]
            self.pool.start(DrawerJob(self.signals, "rahash", rid, lambda: ra_client.identify_row(rid)))
        self._start_ra_detail()

    def _start_ra_detail(self):
        """Achievement list + live numbers for the matched set (from memory if fetched in the last two minutes)."""
        if not self.ra_id:
            return
        memo = self.ra_memo.get(self.ra_id)
        if memo and time.monotonic() - memo[0] < self.RA_REFRESH_SECS:
            self._apply_ra_detail(memo[1], fresh=False)
        else:
            ra_id = self.ra_id
            self.pool.start(DrawerJob(self.signals, "ra", ra_id, lambda: ra_client.refresh_game(ra_id)))

    def _info_ready(self, kind, key, result):
        """A lookup finished (on the UI thread). Answers for a game that is no longer shown are dropped."""
        if kind == "sync":
            if key == self.row_id:
                self._show_sync(None if isinstance(result, JobError) else result)
        elif kind == "hltb":
            self.pending_hltb.discard(key)
            if self.row and self._hltb_key(self.row) == key:
                if isinstance(result, metadata.Metadata) and result.has_data:
                    self._show_hltb(result.main, result.extra, result.complete)
                    self.year.setText(result.year)
                    self.year.setVisible(bool(result.year))
                    self._show_synopsis(result.synopsis)
                else:
                    self._show_hltb(None, None, None)
        elif kind == "rahash":
            if key == self.row_id and isinstance(result, dict):
                try:
                    with db.session() as con:
                        row = db.get_game(con, self.row_id)
                except Exception:
                    return
                if row is not None:
                    self._show_row_info(row)               # verified mark, or the badge removed when RA has no such ROM
                    self._start_ra_detail()
                    if result["changed"]:
                        self.ra_updated.emit()
        elif kind == "ra":
            if isinstance(result, dict):
                self.ra_memo[key] = (time.monotonic(), result)
                if key == self.ra_id:
                    self._apply_ra_detail(result, fresh=True)

    def _show_sync(self, sync):
        if not sync:
            self.sync_badge.hide()
            self.btn_sync.hide()
            self.sync_folder = None
            return
        self.sync_folder = sync["folder"]
        self.sync_badge.setText(engine.syncthing_badge(sync))
        self.sync_badge.setToolTip(f"{sync['file'].name}\n(Syncthing folder: {sync['label']})")
        self.btn_sync.setToolTip(str(sync["file"]))
        self.sync_badge.show()
        self.btn_sync.show()

    def _open_synced_save(self):
        if self.sync_folder:
            err = engine.open_folder(self.sync_folder)
            self.status.emit(err or f"Opened {self.sync_folder}")

    def _apply_ra_detail(self, detail, fresh):
        """Live numbers + achievement list from RetroAchievements. `fresh` = just fetched (and stored in the DB)."""
        if self.ra_id != detail["id"]:
            return
        changed = fresh and self.row is not None and (self.row["ra_unlocked"], self.row["ra_total"],
                                                      self.row["ra_points"]) != (detail["unlocked"], detail["total"], detail["points"])
        self.ra_detail = detail
        self._set_ra_numbers(detail["total"], detail["unlocked"], detail["points"])
        self.ra_box.show()
        if self.ra_toggle.isChecked():
            self._fill_ra_list()
        if changed:
            self.row.update(ra_unlocked=detail["unlocked"], ra_total=detail["total"], ra_points=detail["points"])
            self.ra_updated.emit()

    def _ra_toggled(self, on):
        self.ra_toggle.setText(f"{'▾' if on else '▸'} Achievements ({self.ra_summary_total()})")
        self.ra_list.setVisible(on)
        if on and self.ra_detail:
            self._fill_ra_list()

    def ra_summary_total(self):
        return self.ra_detail["total"] if self.ra_detail else (self.row or {}).get("ra_total", 0)

    def _fill_ra_list(self):
        """One row per achievement: badge icon (arrives from a job), title, points, description, locked/unlocked."""
        detail = self.ra_detail
        if detail is None or self.ra_list.count() == len(detail["achievements"]) and self.ra_items:
            return
        self.ra_list.clear()
        self.ra_items = {}
        blank = QPixmap(40, 40)
        blank.fill(QColor(BG))
        wanted = []
        for a in detail["achievements"]:
            mark = "✔" if a["unlocked"] else "🔒"
            item = QListWidgetItem(QIcon(blank), f"{mark} {a['title']} ({a['points']})\n{a['description']}")
            item.setForeground(QColor(TEXT if a["unlocked"] else DIM))
            item.setToolTip(f"Unlocked {a['date']}" + (" (hardcore)" if a["hardcore"] else "") if a["unlocked"] else "Locked")
            self.ra_list.addItem(item)
            self.ra_items[a["id"]] = item
            wanted.append((a["id"], a["badge"], not a["unlocked"]))
        self.ra_list.setFixedHeight(min(300, 10 + 62 * len(detail["achievements"])))
        dpr = self.devicePixelRatioF()
        self.pool.start(BadgeJob(self.signals, detail["id"], wanted[:150], QSize(int(40 * dpr), int(40 * dpr)),
                                 lambda ra_id: ra_id == self.ra_id))

    def _badge_ready(self, ra_id, ach_id, img):
        item = self.ra_items.get(ach_id)
        if img is None or item is None or ra_id != self.ra_id:
            return
        pix = QPixmap.fromImage(img)
        pix.setDevicePixelRatio(self.devicePixelRatioF())
        item.setIcon(QIcon(pix))

    def forget_ra(self, ra_id):
        """Drop the remembered achievement list of a game (its progress just changed)."""
        self.ra_memo.pop(ra_id, None)

    def _set_poster(self, rows):
        self.token += 1
        self.poster.setPixmap(QPixmap())
        self.poster.setText("…")
        dpr = self.devicePixelRatioF()
        self.pool.start(DrawerPosterTask(self.token, rows, QSize(int(200 * dpr), int(300 * dpr)), self.signals))

    def _poster_ready(self, token, img):
        if token != self.token:
            return
        if img is None:
            self.poster.setText("no cover")
            return
        pix = QPixmap.fromImage(img)
        pix.setDevicePixelRatio(self.devicePixelRatioF())
        self.poster.setPixmap(pix)

    def _open_folder(self):
        if self.folder:
            err = engine.open_folder(self.folder)
            self.status.emit(err or f"Opened {self.folder}")


class VersionModel(QAbstractListModel):
    def __init__(self):
        super().__init__()
        self.items = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.items)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        if role == ITEM_ROLE:
            return self.items[index.row()]
        return self.items[index.row()]["label"] if role == Qt.DisplayRole else None

    def set_items(self, items):
        self.beginResetModel()
        self.items = items
        self.endResetModel()


class VersionDelegate(QStyledItemDelegate):
    """A text card per version: subtitle, file name, then badges for playtime, trophies and kind."""
    CARD = QSize(236, 92)

    def sizeHint(self, option, index):
        return self.CARD

    def paint(self, p, option, index):
        it = index.data(ITEM_ROLE)
        if not it:
            return
        rect = option.rect.adjusted(4, 4, -4, -4)
        hot = bool(option.state & (QStyle.State_MouseOver | QStyle.State_Selected))
        p.save()
        p.fillRect(rect, QColor(PANEL))
        p.setPen(QPen(QColor(ACCENT if hot else LINE), 1))
        p.drawRect(rect.adjusted(0, 0, -1, -1))
        if not it["installed"]:
            p.setOpacity(0.55)
        bold = QFont(option.font)
        bold.setBold(True)
        small = QFont(option.font)
        small.setPointSizeF(max(7.0, option.font.pointSizeF() - 2.0))
        p.setFont(bold)
        p.setPen(QColor(TEXT))
        title = ("★ " if it["favorite"] else "") + it["label"]
        p.drawText(QRect(rect.x() + 8, rect.y() + 5, rect.width() - 16, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(bold).elidedText(title, Qt.ElideRight, rect.width() - 16))
        p.setFont(small)
        p.setPen(QColor(DIM))
        p.drawText(QRect(rect.x() + 8, rect.y() + 24, rect.width() - 16, 16), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(small).elidedText(it["file"], Qt.ElideMiddle, rect.width() - 16))
        fm, x, y = QFontMetrics(small), rect.x() + 8, rect.bottom() - 24
        badges = []
        if it["playtime"]:
            badges.append((f"⏱ {engine.fmt_playtime(it['playtime'])}", TEXT, LINE))
        if it["ra"]:
            text, gold = it["ra"]
            badges.append((text, BG if gold else GOLD, GOLD))
        for kind in sorted(it["kinds"])[:2]:
            badges.append((kind.upper(), DIM, LINE))
        for text, fg, border in badges:
            w = fm.horizontalAdvance(text) + 10
            if x + w > rect.right() - 4:
                break
            r = QRect(x, y, w, fm.height() + 4)
            p.fillRect(r, QColor(GOLD if border == GOLD and fg == BG else BG))
            p.setPen(QPen(QColor(border), 1))
            p.drawRect(r.adjusted(0, 0, -1, -1))
            p.setPen(QColor(fg))
            p.drawText(r, Qt.AlignCenter, text)
            x += w + 4
        p.restore()


class CollectionDialog(QDialog):
    """Browse every version of a big stack (91 Pokémon ROMs and hacks, 27 EarthBounds ...): search, filter by
    kind/tag, see playtime and trophies per version. Double-click launches; a single click shows its notes."""
    launch_requested = Signal(object)         # the games row
    show_requested = Signal(int)              # game id: select it in the details drawer

    SORTS = (("A-Z", "alpha"), ("Playtime", "playtime"), ("Last played", "recent"))

    def __init__(self, group, rows, parent):
        super().__init__(parent)
        self.setWindowTitle(f"{group['base']} — collection")
        self.resize(940, 680)
        self.items = [self._item(r) for r in rows]
        self.active_chips = set()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        head = QHBoxLayout()
        self.heading = QLabel(f"{group['base']}  ·  {len(self.items)} versions")
        self.heading.setObjectName("wizhead")
        head.addWidget(self.heading)
        head.addStretch()
        self.count = QLabel("")
        self.count.setObjectName("dim")
        head.addWidget(self.count)
        lay.addLayout(head)

        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("filter versions…  (name, file, tag)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.apply_filter)
        bar.addWidget(self.search, 1)
        self.sort = QComboBox()
        for label, _mode in self.SORTS:
            self.sort.addItem(f"Sort: {label}")
        self.sort.currentIndexChanged.connect(self.apply_filter)
        bar.addWidget(self.sort)
        lay.addLayout(bar)

        self.chip_host = QWidget()
        self.chip_lay = QHBoxLayout(self.chip_host)
        self.chip_lay.setContentsMargins(0, 0, 0, 0)
        self.chip_lay.setSpacing(4)
        scroller = QScrollArea()
        scroller.setWidget(self.chip_host)
        scroller.setWidgetResizable(True)
        scroller.setFixedHeight(34)
        scroller.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lay.addWidget(scroller)
        self.chips = {}
        counts = {}
        for it in self.items:
            for c in it["chips"]:
                counts[c] = counts.get(c, 0) + 1
        for c in sorted(counts, key=lambda c: (-counts[c], c)):
            b = QPushButton(f"#{c}  {counts[c]}")
            b.setObjectName("tagchip")
            b.setCheckable(True)
            b.setFocusPolicy(Qt.NoFocus)
            b.setToolTip("Show only versions with this tag (several chips show versions with any of them)")
            b.clicked.connect(lambda _c, k=c: self._chip(k))
            self.chip_lay.addWidget(b)
            self.chips[c] = b
        self.chip_lay.addStretch()
        scroller.setVisible(bool(counts))

        self.model = VersionModel()
        self.view = QListView()
        self.view.setModel(self.model)
        self.view.setObjectName("versions")
        self.view.setItemDelegate(VersionDelegate(self.view))
        self.view.setViewMode(QListView.IconMode)
        self.view.setResizeMode(QListView.Adjust)
        self.view.setMovement(QListView.Static)
        self.view.setUniformItemSizes(True)
        self.view.setMouseTracking(True)
        self.view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.view.doubleClicked.connect(self._launch)
        self.view.activated.connect(self._launch)
        lay.addWidget(self.view, 1)

        info = QFrame()
        info.setObjectName("racard")
        iv = QVBoxLayout(info)
        iv.setContentsMargins(10, 8, 10, 8)
        iv.setSpacing(4)
        self.info_title = QLabel("Select a version to see its notes and launch arguments")
        self.info_title.setObjectName("wizhead")
        self.info_title.setWordWrap(True)
        self.info_meta = QLabel("")
        self.info_meta.setObjectName("dim")
        self.info_meta.setWordWrap(True)
        self.info_args = QLabel("")
        self.info_args.setWordWrap(True)
        self.info_notes = QTextEdit()
        self.info_notes.setReadOnly(True)
        self.info_notes.setFixedHeight(64)
        self.info_notes.setPlaceholderText("no notes for this version")
        for w in (self.info_title, self.info_meta, self.info_args, self.info_notes):
            iv.addWidget(w)
        lay.addWidget(info)

        buttons = QHBoxLayout()
        self.btn_launch = QPushButton("▶ Launch")
        self.btn_drawer = QPushButton("📋 Show in details")
        self.btn_close = QPushButton("Close")
        for b in (self.btn_launch, self.btn_drawer, self.btn_close):
            b.setFocusPolicy(Qt.NoFocus)
            buttons.addWidget(b)
        buttons.insertStretch(0)
        lay.addLayout(buttons)
        self.btn_launch.clicked.connect(lambda: self._launch(self.view.currentIndex()))
        self.btn_drawer.clicked.connect(self._show)
        self.btn_close.clicked.connect(self.reject)
        self.view.selectionModel().currentChanged.connect(self._inspect)
        self.apply_filter()
        self.search.setFocus()

    @staticmethod
    def _item(r):
        label = r["version_label"] if r["version_label"] and r["version_label"] != db.DEFAULT_LABEL else r["title"]
        kinds = engine.version_kinds(r)
        file = Path(r["path_or_id"]).name
        tags = db.parse_tags(r["tags"] if "tags" in r.keys() else "")
        ra = engine.ra_badge({"ra_total": r["ra_total"] or 0, "ra_unlocked": r["ra_unlocked"] or 0,
                              "ra_mastered": r["ra_mastered"]}) if "ra_total" in r.keys() else None
        hay = " ".join([label, r["title"], file, *kinds, *tags]).lower()
        return {"row": r, "id": r["id"], "label": label, "file": file, "kinds": kinds, "playtime": r["total_playtime_minutes"] or 0,
                "last_played": r["last_played"] or 0, "ra": ra, "favorite": bool(r["is_favorite"]), "installed": bool(r["is_installed"]),
                "chips": sorted({*kinds, *(engine.tag_key(t) for t in tags)}), "haystack": hay}

    def _chip(self, key):
        self.active_chips ^= {key}
        self.apply_filter()

    def apply_filter(self, *_):
        terms = self.search.text().lower().split()
        mode = self.SORTS[self.sort.currentIndex()][1]
        hits = [it for it in self.items if all(t in it["haystack"] for t in terms)
                and (not self.active_chips or self.active_chips & set(it["chips"]))]
        key = {"alpha": lambda it: it["label"].lower(), "playtime": lambda it: (-it["playtime"], it["label"].lower()),
               "recent": lambda it: (-it["last_played"], it["label"].lower())}[mode]
        hits.sort(key=key)
        for k, b in self.chips.items():
            b.setChecked(k in self.active_chips)
        cur = self.view.currentIndex().data(ITEM_ROLE) if self.view.currentIndex().isValid() else None
        self.model.set_items(hits)
        self.count.setText(f"showing {len(hits)} of {len(self.items)}")
        if cur:
            for i, it in enumerate(hits):
                if it["id"] == cur["id"]:
                    self.view.setCurrentIndex(self.model.index(i))
                    break

    def _inspect(self, index, _prev=None):
        it = index.data(ITEM_ROLE) if index.isValid() else None
        if not it:
            return
        r = it["row"]
        self.info_title.setText(it["label"])
        bits = [it["file"], f"Playtime: {engine.fmt_playtime(it['playtime'])}", f"Last played: {engine.fmt_last_played(it['last_played'])}"]
        if it["ra"]:
            bits.append(it["ra"][0])
        if it["kinds"]:
            bits.append(", ".join("#" + k for k in sorted(it["kinds"])))
        self.info_meta.setText("  |  ".join(bits))
        self.info_args.setText(f"Launch arguments: {r['custom_args']}" if r["custom_args"] else "")
        self.info_args.setVisible(bool(r["custom_args"]))
        self.info_notes.setPlainText(r["notes"] or "")

    def _launch(self, index):
        it = index.data(ITEM_ROLE) if index.isValid() else None
        if it:
            self.launch_requested.emit(it["row"])
            self.accept()

    def _show(self):
        it = self.view.currentIndex().data(ITEM_ROLE) if self.view.currentIndex().isValid() else None
        if it:
            self.show_requested.emit(it["id"])
            self.accept()


class VersionDialog(QDialog):
    """Pick which version of a multi-version game to launch."""

    def __init__(self, group, parent):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.setModal(True)
        self.chosen = None
        order = sorted(range(len(group["rows"])), key=lambda i: (group["rows"][i]["version_label"] != "Default", group["labels"][i].lower()))
        self.rows = [group["rows"][i] for i in order]           # "Default" first and pre-selected
        self.group = group
        self.resize(560, 480)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        head = QLabel(f"{group['base']}")
        head.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {TEXT};")
        sub = QLabel(f"{len(group['rows'])} versions  ·  {tab_text(group['platform'])}  ·  "
                     "Enter to launch, Esc to cancel")
        sub.setStyleSheet(f"color: {DIM};")
        lay.addWidget(head)
        lay.addWidget(sub)

        self.filter_input = None
        if len(group["rows"]) > 6:
            self.filter_input = QLineEdit()
            self.filter_input.setPlaceholderText("Filter versions…")
            self.filter_input.setClearButtonEnabled(True)
            self.filter_input.textChanged.connect(self._refilter)
            lay.addWidget(self.filter_input)

        self.list = QListWidget()
        for i in order:
            self.list.addItem(QListWidgetItem(group["labels"][i]))
        self.list.setCurrentRow(0)
        self.list.itemActivated.connect(lambda _i: self.accept_current())
        lay.addWidget(self.list)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel, go = QPushButton("Cancel"), QPushButton("Launch")
        go.setDefault(True)
        cancel.clicked.connect(self.reject)
        go.clicked.connect(self.accept_current)
        buttons.addWidget(cancel)
        buttons.addWidget(go)
        lay.addLayout(buttons)

        if self.filter_input:
            self.filter_input.setFocus()
        else:
            self.list.setFocus()

    def _refilter(self, text):
        query = text.strip().lower()
        first_visible = None
        for i in range(self.list.count()):
            item = self.list.item(i)
            matches = not query or query in item.text().lower()
            item.setHidden(not matches)
            if matches and first_visible is None:
                first_visible = i
        if first_visible is not None:
            self.list.setCurrentRow(first_visible)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.reject()
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.accept_current()
        elif e.key() == Qt.Key_Down and self.filter_input and self.filter_input.hasFocus():
            self.list.setFocus()
        else:
            super().keyPressEvent(e)

    def accept_current(self):
        item = self.list.currentItem()
        if item and not item.isHidden():
            i = self.list.row(item)
            if 0 <= i < len(self.rows):
                self.chosen = self.rows[i]
                self.accept()


class TitleBar(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setObjectName("titlebar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 0, 0)
        logo = QLabel("WIREPLAY")
        logo.setObjectName("logo")
        sub = QLabel("// game launcher")
        sub.setStyleSheet(f"color: {DIM};")
        lay.addWidget(logo)
        lay.addWidget(sub)
        lay.addStretch()
        for text, fn in (("—", win.showMinimized), ("□", self.toggle_max), ("✕", win.close)):
            b = QPushButton(text)
            b.setObjectName("winbtn")
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(fn)
            lay.addWidget(b)
        self.setFixedHeight(30)

    def toggle_max(self):
        self.win.showNormal() if self.win.isMaximized() else self.win.showMaximized()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.win.windowHandle():
            self.win.windowHandle().startSystemMove()

    def mouseDoubleClickEvent(self, e):
        self.toggle_max()


def _link(text, url):
    label = QLabel(f'<a href="{url}" style="color: {ACCENT};">{text}</a>')
    label.setOpenExternalLinks(True)
    label.setTextInteractionFlags(Qt.TextBrowserInteraction)
    return label


def _secret_edit(value, placeholder=""):
    """A line edit for an API key: dots by default, with a toggle to reveal it."""
    edit = QLineEdit(value or "")
    edit.setEchoMode(QLineEdit.Password)
    edit.setPlaceholderText(placeholder)
    return edit


class PathRow(QWidget):
    """A folder field with Browse… and (optionally) Auto-Detect, plus a one-line verdict on what is in the folder."""
    changed = Signal()

    def __init__(self, kind, detector=None, parent=None):
        super().__init__(parent)
        self.kind, self.detector = kind, detector
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        row = QHBoxLayout()
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("leave empty to auto-detect" if detector else "folder")
        row.addWidget(self.edit, 1)
        self.btn_browse = QPushButton("Browse…")
        self.btn_browse.setFocusPolicy(Qt.NoFocus)
        self.btn_browse.clicked.connect(self._browse)
        row.addWidget(self.btn_browse)
        self.btn_detect = None
        if detector:
            self.btn_detect = QPushButton("🔍 Auto-Detect")
            self.btn_detect.setFocusPolicy(Qt.NoFocus)
            self.btn_detect.clicked.connect(self._detect)
            row.addWidget(self.btn_detect)
        lay.addLayout(row)
        self.note = QLabel("")
        self.note.setObjectName("dim")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        self.edit.textChanged.connect(self._verdict)
        self.edit.textChanged.connect(lambda _t: self.changed.emit())
        self._verdict()

    def text(self):
        return self.edit.text().strip()

    def setText(self, text):
        self.edit.setText(str(text or ""))

    def _verdict(self, *_):
        text = self.text()
        if not text:
            found = self.detector() if self.detector else []
            self.note.setObjectName("dim")
            self.note.setText(f"auto: {found[0]}" if found else "nothing set and nothing detected")
        else:
            path = Path(text).expanduser()
            if not path.is_dir():
                self.note.setObjectName("bad")
                self.note.setText("✗ folder not found (is the drive mounted?)")
            elif self.kind == "roms":
                n = config._rom_score(path)
                self.note.setObjectName("ok" if n else "bad")
                self.note.setText(f"✓ {n} platform folders (nes, snes, gba …)" if n else "• exists, but has no platform folders (nes, snes, gba …)")
            else:
                self.note.setObjectName("ok")
                self.note.setText("✓ found")
        self.note.style().unpolish(self.note)
        self.note.style().polish(self.note)

    def _browse(self):
        start = self.text() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Select folder", start)
        if chosen:
            self.setText(chosen)

    def _detect(self):
        self._pick(self.detector())

    def _pick(self, candidates):
        """Use the detected folder; with several, let the user choose from a small menu."""
        if not candidates:
            self.note.setObjectName("bad")
            self.note.setText("✗ nothing found in the usual places (/run/media, /media, /mnt, ~/Emulation, ~/Games)")
            self.note.style().unpolish(self.note)
            self.note.style().polish(self.note)
            return
        if len(candidates) == 1:
            self.setText(candidates[0])
            return
        menu = QMenu(self)
        actions = {menu.addAction(str(c)): c for c in candidates}
        chosen = menu.exec(self.btn_detect.mapToGlobal(self.btn_detect.rect().bottomLeft())) if self.btn_detect else None
        if chosen in actions:
            self.setText(actions[chosen])


def _api_fields(cfg):
    """The three optional-service blocks shared by Settings and the wizard: SteamGridDB, RetroAchievements, Discord."""
    box = QWidget()
    form = QFormLayout(box)
    form.setContentsMargins(0, 0, 0, 0)
    form.setVerticalSpacing(8)
    sgdb = _secret_edit(cfg.get("steamgriddb_api_key"), "optional: vertical posters for PC games")
    form.addRow("SteamGridDB API key", sgdb)
    form.addRow("", _link("Get a free key at steamgriddb.com/api", "https://www.steamgriddb.com/api"))
    ra_user = QLineEdit(cfg.get("ra_username") or "")
    ra_user.setPlaceholderText("optional: your RetroAchievements username")
    form.addRow("RetroAchievements user", ra_user)
    ra_key = _secret_edit(cfg.get("ra_api_key"), "optional: Web API key")
    form.addRow("RetroAchievements key", ra_key)
    form.addRow("", _link("Find your Web API key at retroachievements.org (Settings → Keys)", "https://retroachievements.org/controlpanel.php"))
    discord = QCheckBox("Enable Discord status (shows the game you are playing)")
    discord.setChecked(bool(cfg.get("discord_rpc", True)))
    form.addRow("", discord)
    return box, sgdb, ra_user, ra_key, discord


class SettingsDialog(QDialog):
    """[⚙ Settings]: library folders, service keys, appearance. values() is what Save writes to config.json."""

    def __init__(self, parent, tabs=()):
        super().__init__(parent)
        self.setWindowTitle("wireplay — Settings")
        self.resize(660, 520)
        cfg = config.load()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)

        # 1. Library paths
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(8)
        v.addWidget(QLabel("ROMs directory"))
        self.roms = PathRow("roms", lambda: [str(p) for p in config.roms_candidates()])
        self.roms.setText(cfg.get("roms_dir"))
        v.addWidget(self.roms)
        v.addWidget(QLabel("Media / boxart directory"))
        self.media = PathRow("media", lambda: [str(p) for p in config.media_candidates(config.get_roms_dir() if not self.roms.text() else Path(self.roms.text()).expanduser())])
        self.media.setText(cfg.get("media_dir"))
        v.addWidget(self.media)
        v.addWidget(self._caption("Detected launchers"))
        status = engine.integration_status()
        self.steam_label, self.heroic_label = QLabel(status["steam"][1]), QLabel(status["heroic"][1])
        for label, key in ((self.steam_label, "steam"), (self.heroic_label, "heroic")):
            label.setObjectName("ok" if status[key][0] else "dim")
            label.setWordWrap(True)
            v.addWidget(label)
        hint = QLabel("Changing a folder rescans your library. Steam and Heroic are detected automatically; set "
                      "\"steam_dir\" / \"heroic_dir\" in config.json for a non-standard install.")
        hint.setObjectName("dim")
        hint.setWordWrap(True)
        v.addWidget(hint)
        v.addStretch()
        self.tabs.addTab(page, "Library Paths")

        # 2. Integrations
        page = QWidget()
        v = QVBoxLayout(page)
        fields, self.sgdb, self.ra_user, self.ra_key, self.discord = _api_fields(cfg)
        v.addWidget(fields)
        v.addStretch()
        self.tabs.addTab(page, "Integrations && API Keys")           # && : a single & would become a keyboard mnemonic

        # 3. Appearance & behavior
        page = QWidget()
        form = QFormLayout(page)
        form.setVerticalSpacing(12)
        row = QHBoxLayout()
        saved = cfg.get("card_width")
        width = clamp_poster_w(saved) if isinstance(saved, (int, float)) and not isinstance(saved, bool) else DEFAULT_POSTER_W
        self.width_slider = QSlider(Qt.Horizontal)
        self.width_slider.setRange(MIN_POSTER_W, MAX_POSTER_W)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(MIN_POSTER_W, MAX_POSTER_W)
        self.width_spin.setSuffix(" px")
        self.width_slider.valueChanged.connect(self.width_spin.setValue)
        self.width_spin.valueChanged.connect(self.width_slider.setValue)
        self.width_spin.setValue(width)
        row.addWidget(self.width_slider, 1)
        row.addWidget(self.width_spin)
        form.addRow("Default card width", row)
        self.startup = QComboBox()
        self.startup.addItem("All", "all")
        self.startup.addItem("★ Favorites", "favorites")
        for key, label in tabs:
            self.startup.addItem(label, key)
        i = self.startup.findData(cfg.get("startup_tab") or "all")
        self.startup.setCurrentIndex(max(0, i))
        form.addRow("Startup tab", self.startup)
        self.tabs.addTab(page, "Appearance && Behavior")

        buttons = QHBoxLayout()
        self.version_label = QLabel(f"wireplay {__version__}")
        self.version_label.setObjectName("dim")
        buttons.addWidget(self.version_label)
        buttons.addStretch()
        self.btn_cancel = QPushButton("Cancel")
        self.btn_save = QPushButton("Save")
        self.btn_save.setObjectName("primary")
        self.btn_save.setDefault(True)
        for b in (self.btn_cancel, self.btn_save):
            b.setFocusPolicy(Qt.NoFocus)
            buttons.addWidget(b)
        lay.addLayout(buttons)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_save.clicked.connect(self.accept)

    @staticmethod
    def _caption(text):
        label = QLabel(text.upper())
        label.setObjectName("dim")
        return label

    def values(self):
        return {
            "roms_dir": self.roms.text(), "media_dir": self.media.text(),
            "steamgriddb_api_key": self.sgdb.text().strip(),
            "ra_username": self.ra_user.text().strip(), "ra_api_key": self.ra_key.text().strip(),
            "discord_rpc": self.discord.isChecked(),
            "card_width": self.width_spin.value(), "startup_tab": self.startup.currentData() or "all",
        }


class FirstRunWizard(QDialog):
    """Three steps for a new installation: welcome, what was found (launchers + ROM folder), optional keys."""
    STEPS = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Welcome to wireplay")
        self.resize(640, 500)
        cfg = config.load()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 16)
        lay.setSpacing(10)
        self.logo = QLabel("WIREPLAY")
        self.logo.setObjectName("wizlogo")
        lay.addWidget(self.logo)
        self.step_label = QLabel("")
        self.step_label.setObjectName("wizstep")
        lay.addWidget(self.step_label)
        self.pages = QStackedWidget()
        lay.addWidget(self.pages, 1)

        # 1. welcome
        page = QWidget()
        v = QVBoxLayout(page)
        title = QLabel("Welcome to Wireplay // Game Launcher")
        title.setObjectName("wizhead")
        title.setWordWrap(True)
        v.addWidget(title)
        intro = QLabel("One launcher for your Steam library, Epic games (through Heroic), Minecraft instances and your "
                       "retro ROM collection. Posters, playtime, achievements and save-sync status come together in one grid.\n\n"
                       "Setup takes a minute: wireplay looks for your launchers and ROM folder, and you can add optional "
                       "service keys. Everything can be changed later in ⚙ Settings.")
        intro.setWordWrap(True)
        v.addWidget(intro)
        v.addStretch()
        self.pages.addWidget(page)

        # 2. detected platforms
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(8)
        head = QLabel("Detected platforms")
        head.setObjectName("wizhead")
        v.addWidget(head)
        status = engine.integration_status()
        for key in ("steam", "heroic"):
            label = QLabel(status[key][1])
            label.setObjectName("ok" if status[key][0] else "dim")
            label.setWordWrap(True)
            v.addWidget(label)
        v.addWidget(QLabel("ROM folder (the one that contains nes/, snes/, gba/ …)"))
        self.roms = PathRow("roms", lambda: [str(p) for p in config.roms_candidates()])
        self.media = PathRow("media")
        found = config.roms_candidates()
        self.roms.setText(cfg.get("roms_dir") or (str(found[0]) if found else ""))
        v.addWidget(self.roms)
        v.addWidget(QLabel("Box art folder (optional)"))
        v.addWidget(self.media)
        self.media.setText(cfg.get("media_dir") or self._guess_media())
        self.roms.edit.textChanged.connect(self._roms_changed)
        v.addStretch()
        self.pages.addWidget(page)

        # 3. optional keys
        page = QWidget()
        v = QVBoxLayout(page)
        head = QLabel("Optional services")
        head.setObjectName("wizhead")
        v.addWidget(head)
        note = QLabel("All optional. Leave anything blank to skip it.")
        note.setObjectName("dim")
        v.addWidget(note)
        fields, self.sgdb, self.ra_user, self.ra_key, self.discord = _api_fields(cfg)
        v.addWidget(fields)
        v.addStretch()
        self.pages.addWidget(page)

        buttons = QHBoxLayout()
        self.btn_skip = QPushButton("Skip setup")
        self.btn_back = QPushButton("← Back")
        self.btn_next = QPushButton("Next →")
        self.btn_next.setObjectName("primary")
        buttons.addWidget(self.btn_skip)
        buttons.addStretch()
        buttons.addWidget(self.btn_back)
        buttons.addWidget(self.btn_next)
        for b in (self.btn_skip, self.btn_back, self.btn_next):
            b.setFocusPolicy(Qt.NoFocus)
        lay.addLayout(buttons)
        self.btn_skip.clicked.connect(self.reject)
        self.btn_back.clicked.connect(lambda: self.go(self.pages.currentIndex() - 1))
        self.btn_next.clicked.connect(self._next)
        self.go(0)

    def _guess_media(self):
        roms = Path(self.roms.text()).expanduser() if self.roms.text() else None
        found = config.media_candidates(roms)
        return str(found[0]) if found else ""

    def _roms_changed(self, _text):
        if not self.media.text():                       # follow the ROM folder until the user picks an art folder
            self.media.setText(self._guess_media())

    def go(self, index):
        index = max(0, min(self.STEPS - 1, index))
        self.pages.setCurrentIndex(index)
        self.step_label.setText("   ".join(("●" if i == index else "○") for i in range(self.STEPS)) + f"    step {index + 1} of {self.STEPS}")
        self.btn_back.setVisible(index > 0)
        self.btn_next.setText("Finish && scan" if index == self.STEPS - 1 else "Next →")

    def _next(self):
        if self.pages.currentIndex() == self.STEPS - 1:
            self.accept()
        else:
            self.go(self.pages.currentIndex() + 1)

    def values(self):
        return {
            "roms_dir": self.roms.text(), "media_dir": self.media.text(),
            "steamgriddb_api_key": self.sgdb.text().strip(),
            "ra_username": self.ra_user.text().strip(), "ra_api_key": self.ra_key.text().strip(),
            "discord_rpc": self.discord.isChecked(),
        }

    def save(self, finished=True):
        """Write the setup to config.json (finished=False: Skip/close, keep detection only) and end the first run."""
        values = self.values() if finished else {}
        ok = config.update(first_run=False, **values)
        config.ensure_paths()
        return ok


def run_first_run_wizard():
    """Show the wizard for a fresh install (no config.json, or "first_run": true). Returns True if it was shown."""
    if not config.first_run_needed():
        return False
    wizard = FirstRunWizard()
    wizard.save(finished=wizard.exec() == QDialog.Accepted)
    return True


class Window(QWidget):
    def __init__(self):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint)
        self.setWindowTitle("wireplay")
        self.resize(1280, 820)
        self.signals = _Signals()
        self.pool = QThreadPool(self)                  # posters + rescans
        self.pool.setMaxThreadCount(4)
        self.ui_pool = QThreadPool(self)               # cover-picker work, so it never queues behind 100 posters
        self.ui_pool.setMaxThreadCount(3)
        self.signals.poster.connect(self._poster_ready)
        self.signals.dropped.connect(self._poster_dropped)
        self.signals.scanned.connect(self._scanned)
        self.cache = PosterCache()
        self.epoch = [0]                               # bumped whenever the visible set changes (see PosterTask)
        self.all_groups, self.active, self.last_launch = [], None, 0.0
        self.installed_only, self.scanning = False, False
        self.sort_by = "alpha"
        self.sessions = SessionWatcher(self)
        self.sessions.finished.connect(self._session_finished)
        self.sessions.ended.connect(self._session_ended)
        self.sessions.discarded.connect(self._session_discarded)
        self.sessions.beat.connect(self._session_beat)
        self.presence = discord_rpc.Presence()          # never blocks or raises; silent when Discord is not running
        self.presence_game = None                       # id of the game the Discord status currently shows
        config.ensure_paths()                           # an existing config.json gets the detected ROM / art folders
        saved = config.load().get("card_width")
        self.zoom_f = float(clamp_poster_w(saved)) if isinstance(saved, (int, float)) and not isinstance(saved, bool) \
            else float(DEFAULT_POSTER_W)
        self.zoom_timer = QTimer(self)                  # fires once the user stops zooming
        self.zoom_timer.setSingleShot(True)
        self.zoom_timer.setInterval(250)
        self.zoom_timer.timeout.connect(self._zoom_settled)
        self.drawer = Drawer(self.ui_pool)
        self.drawer.favorite_clicked.connect(self.toggle_favorite)
        self.drawer.status.connect(self.status_text)
        self.drawer.tags_changed.connect(self.reload)         # new tags: refresh groups and the collection chips
        self.drawer.ra_updated.connect(self.reload)           # fresh achievement numbers: refresh the card badges
        self.signals.ra_synced.connect(self._ra_synced)
        self.signals.ra_game.connect(self._ra_game_done)
        self.ra_syncing, self.force_ra = False, False
        self.drawer.tag_clicked.connect(self.toggle_tag_filter)
        self.drawer.browse_requested.connect(self.open_collection)
        self.known_tag_keys, self.tag_chips = frozenset(), {}
        self.quick, self.quick_chips = None, {}                # quick filter: None | "recent" | "never"
        self.recovery_note = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)
        root.addWidget(TitleBar(self))

        top = QVBoxLayout()
        top.setContentsMargins(12, 10, 12, 8)
        top.setSpacing(8)

        # Action bar buttons
        actions = QHBoxLayout()
        actions.setSpacing(6)

        self.btn_rescan = self._action_button("🔄 Rescan", "Rescan Steam, shortcuts, Minecraft, and ROMs",
                                              self.trigger_rescan)
        self.btn_installed = self._action_button("📦 Installed Only: OFF",
                                                 "Toggle between showing all owned games vs installed only",
                                                 None)
        self.btn_installed.setCheckable(True)
        self.btn_installed.toggled.connect(self.set_installed_only)
        zoom_box = QWidget()                              # compact  [−] 100% [+]
        zoom_lay = QHBoxLayout(zoom_box)
        zoom_lay.setContentsMargins(0, 0, 0, 0)
        zoom_lay.setSpacing(0)
        self.btn_zoom_out = self._action_button("−", "Smaller cards (Ctrl+-)", lambda: self.zoom_to(self.zoom_f - ZOOM_STEP))
        self.btn_zoom_reset = self._action_button("100%", "Reset card size (Ctrl+0)", lambda: self.zoom_to(DEFAULT_POSTER_W))
        self.btn_zoom_in = self._action_button("+", "Larger cards (Ctrl+= / Ctrl++, or Ctrl+mouse wheel)",
                                               lambda: self.zoom_to(self.zoom_f + ZOOM_STEP))
        for b, width in ((self.btn_zoom_out, 28), (self.btn_zoom_reset, 54), (self.btn_zoom_in, 28)):
            b.setFixedWidth(width)
            b.setStyleSheet("padding: 4px 0;")
            zoom_lay.addWidget(b)
        self.btn_random = self._action_button("🎲 Random Game", "Pick a random game from the current list",
                                              self.pick_random_game)
        self.btn_cover = self._action_button("🎨 Change Cover", "Change cover art for the selected game",
                                             self.open_cover_picker_selected)
        self.btn_sort = self._action_button("🔀 Sort: A-Z", "Cycle sort order: A-Z → Recent → Playtime",
                                            self.cycle_sort)
        self.btn_details = self._action_button("📋 Details", "Show/hide the game details panel (Space or I)", None)
        self.btn_details.setCheckable(True)
        self.btn_details.toggled.connect(self.set_drawer)
        self.btn_settings = self._action_button("⚙️ Settings", "Library folders, API keys and appearance (Ctrl+,)",
                                                self.open_settings)
        QShortcut(QKeySequence("Ctrl+,"), self, activated=self.open_settings)
        for b in (self.btn_rescan, self.btn_installed, self.btn_sort, zoom_box, self.btn_random, self.btn_cover,
                  self.btn_details, self.btn_settings):
            actions.addWidget(b)
        actions.addStretch()
        top.addLayout(actions)

        self.search = QLineEdit()
        self.search.setPlaceholderText("search…   (#rpg = tag)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refilter)
        top.addWidget(self.search)

        self.tabs_host = QWidget()
        self.tabs_lay = QHBoxLayout(self.tabs_host)
        self.tabs_lay.setContentsMargins(0, 0, 0, 0)
        self.tabs_lay.setSpacing(4)
        scroller = QScrollArea()                      # platform buttons scroll sideways when they overflow
        scroller.setWidget(self.tabs_host)
        scroller.setWidgetResizable(True)
        scroller.setFixedHeight(46)
        scroller.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        top.addWidget(scroller)
        self.chips_host = QWidget()                   # collection chips: one per tag in use, click to filter
        self.chips_lay = QHBoxLayout(self.chips_host)
        self.chips_lay.setContentsMargins(0, 0, 0, 0)
        self.chips_lay.setSpacing(4)
        self.chips_scroll = QScrollArea()
        self.chips_scroll.setWidget(self.chips_host)
        self.chips_scroll.setWidgetResizable(True)
        self.chips_scroll.setFixedHeight(34)
        self.chips_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.chips_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        top.addWidget(self.chips_scroll)

        # Recently Played shelf: the last few games with play history, one click to resume
        self.shelf_box = QWidget()
        sv = QVBoxLayout(self.shelf_box)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(2)
        self.shelf_toggle = QPushButton("▾ Recently Played")
        self.shelf_toggle.setObjectName("shelftoggle")
        self.shelf_toggle.setFlat(True)
        self.shelf_toggle.setCheckable(True)
        self.shelf_toggle.setFocusPolicy(Qt.NoFocus)
        self.shelf_toggle.setToolTip("Show / hide the recently played shelf (double-click or Enter on a game resumes it)")
        sv.addWidget(self.shelf_toggle, 0, Qt.AlignLeft)
        self.shelf_model = GroupModel()
        self.shelf_view = QListView()
        self.shelf_view.setModel(self.shelf_model)
        self.shelf_view.setFlow(QListView.LeftToRight)
        self.shelf_view.setWrapping(False)
        self.shelf_view.setFrameShape(QFrame.NoFrame)
        self.shelf_view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.shelf_view.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.shelf_view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.shelf_view.setUniformItemSizes(True)
        self.shelf_view.setMouseTracking(True)
        sv.addWidget(self.shelf_view)
        self.shelf_box.hide()                          # shown once something has been played
        top.addWidget(self.shelf_box)
        root.addLayout(top)
        self.btn_group = QButtonGroup(self)
        self.btn_group.setExclusive(True)
        self.tab_buttons = {}                          # platform (None = All) -> button

        self.model = GroupModel()
        self.view = PosterView()
        self.view.setModel(self.model)
        self.delegate = CardDelegate(self.view, self.cache, self.pool, self.signals, self.epoch)
        self.view.setItemDelegate(self.delegate)
        self.delegate.set_poster_w(clamp_poster_w(self.zoom_f))
        self.delegate.settled = True
        self.shelf_delegate = CardDelegate(self.shelf_view, self.cache, self.pool, self.signals, self.epoch)
        self.shelf_delegate.set_poster_w(SHELF_POSTER_W)
        self.shelf_delegate.settled = True
        self.shelf_view.setItemDelegate(self.shelf_delegate)
        self.shelf_view.setFixedHeight(self.shelf_delegate.card_h + 14)
        collapsed = bool(config.load().get("shelf_collapsed"))
        self.shelf_toggle.setChecked(not collapsed)
        self.shelf_toggle.setText(f"{'▸' if collapsed else '▾'} Recently Played")
        self.shelf_toggle.toggled.connect(self._shelf_toggled)
        self.shelf_view.doubleClicked.connect(self._shelf_resume)
        self.shelf_view.activated.connect(self._shelf_resume)
        self.shelf_view.clicked.connect(self._shelf_clicked)
        self.view.zoom_by.connect(lambda px: self.zoom_to(self.zoom_f + px))
        for keys, delta in ((("Ctrl+=", "Ctrl++", "Ctrl+Shift+=", "Ctrl+Shift++"), ZOOM_STEP), (("Ctrl+-",), -ZOOM_STEP)):
            for key in keys:
                QShortcut(QKeySequence(key), self, activated=lambda d=delta: self.zoom_to(self.zoom_f + d))
        QShortcut(QKeySequence("Ctrl+0"), self, activated=lambda: self.zoom_to(DEFAULT_POSTER_W))
        self._update_zoom_label()
        self.view.activate_group.connect(self.activate)
        self.view.request_cover.connect(self.open_cover_picker)
        self.view.toggle_favorite.connect(self.toggle_favorite)
        self.view.browse_collection.connect(self.open_collection)
        self.view.toggle_details.connect(lambda: self.btn_details.setChecked(not self.btn_details.isChecked()))
        self.view.show_details.connect(lambda: self.btn_details.setChecked(True))
        self.view.selectionModel().currentChanged.connect(self._current_changed)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self.view, 1)
        body.addWidget(self.drawer)
        self.drawer.setMaximumWidth(0)                  # starts collapsed
        self.drawer.hide()
        self._anim = QPropertyAnimation(self.drawer, b"maximumWidth", self)   # one animation, reused for every toggle
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._keep_scroll)
        self._anim.finished.connect(self._drawer_anim_done)
        self._scroll_pos = 0
        root.addLayout(body, 1)

        bar = QHBoxLayout()
        bar.setContentsMargins(12, 4, 2, 2)
        self.status = QLabel("")
        self.status.setObjectName("status")
        bar.addWidget(self.status, 1)
        bar.addWidget(QSizeGrip(self), 0, Qt.AlignBottom | Qt.AlignRight)
        root.addLayout(bar)

        self._reconcile_sessions()                     # settle / resume sessions left open by the last run
        self.load_games()
        startup = config.load().get("startup_tab")
        if isinstance(startup, str) and startup not in ("", "all"):
            wanted = FAV if startup == "favorites" else startup
            if wanted in self.tab_buttons:
                self.set_tab(wanted)
        if self.recovery_note:
            self.status.setText(self.recovery_note)
        self.trigger_rescan(quiet=True)                # refresh the DB in the background
        self.search.setFocus()

    @staticmethod
    def _action_button(text, tip, slot):
        b = QPushButton(text)
        b.setFocusPolicy(Qt.NoFocus)
        b.setToolTip(tip)
        if slot:
            b.clicked.connect(slot)
        return b

    # -- data
    def load_games(self):
        with db.session() as con:
            games = db.get_games(con, installed_only=self.installed_only, sort_by=self.sort_by)
        self.all_groups = engine.group_games(games, self.sort_by)
        self.build_tabs()
        self.build_tag_chips()
        self.refilter()

    def build_tabs(self):
        names = [FAV, *engine.tab_names(self.all_groups)]     # ★ Favorites is the first tab
        if self.active not in names:                    # that platform has no games any more
            self.active = None
        while self.tabs_lay.count():                    # drop old buttons and the trailing stretch
            item = self.tabs_lay.takeAt(0)
            if item.widget():
                self.btn_group.removeButton(item.widget())
                item.widget().deleteLater()
        self.tab_buttons = {}
        for p in names:
            b = QPushButton(tab_text(p))
            b.setCheckable(True)
            b.setFocusPolicy(Qt.NoFocus)
            b.setChecked(p == self.active)
            b.clicked.connect(lambda _c, p=p: self.set_tab(p))
            self.btn_group.addButton(b)
            self.tabs_lay.addWidget(b)
            self.tab_buttons[p] = b
        self.tabs_lay.addStretch()

    def build_tag_chips(self):
        """One checkable chip per tag in use (most used first). Hidden while no game has a tag."""
        counts, names = {}, {}
        for g in self.all_groups:
            for tag in g["tags"]:
                key = engine.tag_key(tag)
                names.setdefault(key, tag)
                counts[key] = counts.get(key, 0) + 1
        order = sorted(counts, key=lambda k: (-counts[k], k))
        self.known_tag_keys = frozenset(counts)
        self.drawer.set_known_tags([names[k] for k in order])
        while self.chips_lay.count():
            item = self.chips_lay.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self.tag_chips = {}
        self.quick_chips = {}
        for mode, text, tip in (("recent", "🕒 Played Recently", f"Games played in the last {engine.RECENT_DAYS} days"),
                                ("never", "📦 Never Played", "Installed games you have not played yet (the backlog)")):
            n = sum(1 for g in self.all_groups if engine.quick_match(g, mode))
            b = QPushButton(f"{text}  {n}")
            b.setObjectName("tagchip")
            b.setCheckable(True)
            b.setFocusPolicy(Qt.NoFocus)
            b.setToolTip(f"{tip} (click again to clear)")
            b.clicked.connect(lambda _c, m=mode: self.set_quick(m))
            self.chips_lay.addWidget(b)
            self.quick_chips[mode] = b
        for key in order:
            b = QPushButton(f"#{names[key]}  {counts[key]}")
            b.setObjectName("tagchip")
            b.setCheckable(True)
            b.setFocusPolicy(Qt.NoFocus)
            b.setToolTip(f"Show only games tagged {names[key]} (click again to clear)")
            b.clicked.connect(lambda _c, k=key: self.toggle_tag_filter(k))
            self.chips_lay.addWidget(b)
            self.tag_chips[key] = b
        self.chips_lay.addStretch()
        self.chips_scroll.show()
        self._refresh_shelf()

    def set_quick(self, mode):
        """Toggle a quick filter (only one at a time); it combines with the tab, search and #tags."""
        self.quick = None if self.quick == mode else mode
        self.refilter()

    # -- recently played shelf
    def _refresh_shelf(self):
        groups = engine.shelf_groups(self.all_groups)
        self.shelf_model.set_groups(groups)
        self.shelf_box.setVisible(bool(groups))
        self.shelf_view.setVisible(bool(groups) and self.shelf_toggle.isChecked())
        self.shelf_view.viewport().update()

    def _shelf_toggled(self, expanded):
        self.shelf_toggle.setText(f"{'▾' if expanded else '▸'} Recently Played")
        self.shelf_view.setVisible(expanded and self.shelf_model.rowCount() > 0)
        config.update(shelf_collapsed=not expanded)

    def _shelf_group(self, index):
        g = index.data(GROUP_ROLE) if index.isValid() else None
        return g

    def _shelf_resume(self, index):
        """Resume the game: launch the version that was played last."""
        g = self._shelf_group(index)
        row = engine.resume_row(g["orig"]) if g else None
        if row is not None:
            self.launch_row(row)

    def _shelf_clicked(self, index):
        g = self._shelf_group(index)
        if g and self.btn_details.isChecked():
            self.drawer.set_group(g["orig"])

    def toggle_tag_filter(self, key):
        """Add or remove `#key` in the search box (which then refilters the grid)."""
        words = self.search.text().split()
        rest = [w for w in words if not (w.startswith("#") and engine.tag_key(w.lstrip("#")) == key)]
        if len(rest) == len(words):
            rest.append(f"#{key}")
        self.search.setText(" ".join(rest))

    def set_tab(self, p):
        self.active = p
        if p in self.tab_buttons:
            self.tab_buttons[p].setChecked(True)
        self.refilter()

    def refilter(self):
        terms, wanted = engine.parse_query(self.search.text())
        for key, chip in self.tag_chips.items():          # chips mirror the #tags typed in the search box
            chip.setChecked(key in wanted)
        for mode, chip in self.quick_chips.items():
            chip.setChecked(mode == self.quick)
        hits = [g for g in self.all_groups
                if (self.active is None or (g["favorite"] if self.active == FAV else g["platform"] == self.active))
                and all(t in g["haystack"] for t in terms)
                and engine.has_tags(g, wanted, self.known_tag_keys)
                and engine.quick_match(g, self.quick)]
        hits = engine.sort_groups(hits, self.sort_by)
        self.epoch[0] += 1                              # tasks queued for the old view are now stale
        self.model.set_groups(hits)
        mode = "installed" if self.installed_only else "all"
        self.status.setText(f"{len(hits)} games ({mode})")

    def set_installed_only(self, on):
        self.installed_only = on
        self.btn_installed.setText(f"📦 Installed Only: {'ON' if on else 'OFF'}")
        self.load_games()

    SORT_LABELS = {"alpha": "A-Z", "recent": "🕒 Recent", "playtime": "⏳ Playtime"}

    def cycle_sort(self):
        modes = engine.SORT_MODES
        self.sort_by = modes[(modes.index(self.sort_by) + 1) % len(modes)]
        self.btn_sort.setText(f"🔀 Sort: {self.SORT_LABELS[self.sort_by]}")
        self.reload()

    def reload(self):
        """Re-read the DB and refilter, keeping the grid's scroll position and current card."""
        bar = self.view.verticalScrollBar()
        pos = bar.value()
        cur = self.view.currentIndex().data(GROUP_ROLE) if self.view.currentIndex().isValid() else None
        self.load_games()
        if cur:
            for i, g in enumerate(self.model.groups):
                if g["key"] == cur["key"]:
                    self.view.setCurrentIndex(self.model.index(i))
                    break
        QTimer.singleShot(0, lambda: bar.setValue(pos))

    def _update_zoom_label(self):
        pct = int(self.delegate.poster_w * 100 / DEFAULT_POSTER_W + 0.5)    # round half up: 260 -> 163%
        self.btn_zoom_reset.setText(f"{pct}%")
        self.btn_zoom_out.setEnabled(self.delegate.poster_w > MIN_POSTER_W)
        self.btn_zoom_in.setEnabled(self.delegate.poster_w < MAX_POSTER_W)

    def _top_visible_index(self):
        """A card near the viewport's top-left corner (probes several points: one may fall in a gap between cards)."""
        for y in (8, 40, 80, 130, 200, 300):
            for x in (12, 40, 90, 150):
                idx = self.view.indexAt(QPoint(x, y))
                if idx.isValid():
                    return idx
        return QModelIndex()

    def zoom_to(self, width):
        """Resize the cards live. The grid reflows in place (no reload) and keeps the first visible card on top."""
        self.zoom_f = float(max(MIN_POSTER_W, min(MAX_POSTER_W, width)))   # fractions accumulate for smooth wheels
        w = clamp_poster_w(self.zoom_f)
        if w == self.delegate.poster_w:
            return
        anchor = self._top_visible_index()
        self.delegate.set_poster_w(w)
        self.epoch[0] += 1                                  # renders queued for the old size are stale
        self.delegate.sizeHintChanged.emit(self.model.index(0))
        self.view.doItemsLayout()
        if anchor.isValid():
            for delay in (0, 90):                           # again after the batched layout has caught up
                QTimer.singleShot(delay, lambda: self.view.scrollTo(anchor, QAbstractItemView.PositionAtTop))
        self.view.viewport().update()
        self._update_zoom_label()
        self.zoom_timer.start()

    def _zoom_settled(self):
        """Zooming stopped: re-render visible posters at the new size and remember the setting."""
        self.delegate.settled = True
        self.view.viewport().update()
        config.update(card_width=self.delegate.poster_w)

    def status_text(self, text):
        self.status.setText(text)

    def toggle_favorite(self, group):
        try:
            with db.session() as con:
                state = db.toggle_favorite(con, group["base"], group["platform"])
        except Exception as e:
            self.status.setText(f"Could not update favorite: {e}")
            return
        self.status.setText(f"{'★ Added to' if state else '☆ Removed from'} favorites: {group['base']}")
        self.reload()

    def set_drawer(self, on):
        """Slide the details panel in/out. The grid keeps its scroll position through the resize."""
        bar = self.view.verticalScrollBar()
        if self._anim.state() == QAbstractAnimation.Running:   # toggled mid-slide: reverse from where it is
            self._anim.stop()
        else:
            self._scroll_pos = bar.value()
        if on:
            self.drawer.show()
            idx = self.view.currentIndex()
            if not idx.isValid() and self.model.rowCount():
                self.view.setCurrentIndex(self.model.index(0))
            else:
                self._current_changed(idx)
        else:
            self.drawer.flush()
        self._anim.setStartValue(self.drawer.maximumWidth())
        self._anim.setEndValue(DRAWER_W if on else 0)
        self._anim.start()

    def _keep_scroll(self, _value):
        self.view.verticalScrollBar().setValue(self._scroll_pos)

    def _drawer_anim_done(self):
        """Runs after every slide; looks at the *current* toggle state, so a stale 'close' can never hide an open drawer."""
        if not self.btn_details.isChecked():
            self.drawer.hide()
        self.view.verticalScrollBar().setValue(self._scroll_pos)

    def _current_changed(self, idx, _prev=None):
        if self.btn_details.isChecked():
            self.drawer.set_group(idx.data(GROUP_ROLE) if idx.isValid() else None)

    def _session_ended(self, game_id):
        if game_id == self.presence_game:
            self.presence_game = None
            self.presence.clear()

    def _session_finished(self, sid, game_id, minutes, end_epoch):
        try:
            with db.session() as con:
                if sid:
                    db.finish_session(con, sid, minutes, end_time=end_epoch)
                else:                                      # the sessions row could not be created at launch
                    db.record_session(con, game_id, minutes)
        except Exception as e:
            self.status.setText(f"Could not record play session: {e}")
            return
        self.reload()                                      # first: it rewrites the status line
        self.status.setText(f"Session recorded: {engine.fmt_playtime(minutes)}")
        self._refresh_achievements(game_id)

    def _refresh_achievements(self, game_id):
        """After playing a retro game its achievement progress may have changed: refetch it in the background."""
        if not ra_client.enabled():
            return
        try:
            with db.session() as con:
                row = db.get_game(con, game_id)
        except Exception:
            return
        if row is not None and row["ra_game_id"]:
            self.ui_pool.start(RaGameTask(self.signals, row["ra_game_id"]))

    def _session_discarded(self, sid):
        try:
            with db.session() as con:
                db.discard_session(con, sid)
        except Exception:
            pass                                           # an open row is settled by the next startup

    def _session_beat(self, sid, pid, pid_start, now):
        try:
            with db.session() as con:
                db.touch_session(con, sid, now, pid or None, pid_start or None)
        except Exception:
            pass                                           # a missed heartbeat only costs up to BEAT_SECS on a crash

    def _reconcile_sessions(self):
        """Startup: settle sessions that were open when wireplay last quit. Running games are watched again."""
        try:
            with db.session() as con:
                resumed, closed = engine.reconcile_sessions(con)
        except Exception as e:
            self.status.setText(f"Could not check unfinished sessions: {e}")
            return
        for game, tracker in resumed:
            self.sessions.adopt(tracker)
            self.presence.set(engine.presence_title(game), engine.platform_name(game["platform"]), tracker.start_wall)
            self.presence_game = game["id"]
        notes = []
        if closed:
            notes.append(f"recovered {len(closed)} unfinished session(s): "
                         + engine.fmt_playtime(sum(m for _g, m in closed)))
        if resumed:
            notes.append(f"still tracking {len(resumed)} running game(s)")
        self.recovery_note = "; ".join(notes)

    def start_ra_sync(self):
        """Match the library to RetroAchievements in the background (only when keys are configured; at most every
        few hours unless the user pressed Rescan). A failure is silent: the cached badges simply stay."""
        if self.ra_syncing or not ra_client.enabled() or not (self.force_ra or ra_client.sync_due()):
            return
        self.ra_syncing, self.force_ra = True, False
        self.ui_pool.start(RaSyncTask(self.signals))

    def _ra_synced(self, count, error):
        self.ra_syncing = False
        if not error and count:
            self.reload()                                  # the cards now have 🏆 badges

    def _ra_game_done(self, ra_id):
        self.drawer.forget_ra(ra_id)
        self.reload()

    def trigger_rescan(self, quiet=False):
        if self.scanning:
            return
        self.force_ra = not quiet                          # a manual Rescan also refreshes RetroAchievements
        self.scanning = True
        self.btn_rescan.setEnabled(False)
        if not quiet:
            self.status.setText("🔄 Rescanning library in background…")
        self.pool.start(ScanTask(self.signals))

    def pick_random_game(self):
        count = self.model.rowCount()
        if not count:
            self.status.setText("No games available to pick from")
            return
        idx = self.model.index(random.randrange(count))
        self.view.setCurrentIndex(idx)
        self.view.scrollTo(idx, QAbstractItemView.PositionAtCenter)
        g = idx.data(GROUP_ROLE)
        self.status.setText(f"🎲 Random pick: {g['base']} [{tab_text(g['platform'])}]")

    def open_cover_picker_selected(self):
        g = self.view.currentIndex().data(GROUP_ROLE) if self.view.currentIndex().isValid() else None
        if g:
            self.open_cover_picker(g)
        else:
            self.status.setText("Select a game first to change its cover")

    def _run_dialog(self, dlg):
        dlg.move(self.geometry().center() - dlg.rect().center())
        accepted = dlg.exec() == QDialog.Accepted
        dlg.deleteLater()                               # it is parented to this window: free it now
        return accepted

    def open_cover_picker(self, group):
        dlg = CoverPickerDialog(group, self, self.ui_pool)
        if self._run_dialog(dlg) and dlg.applied:
            self.cache.discard(group["key"])
            self.view.viewport().update()               # repaint requests the new art
            self.status.setText(f"🎨 Updated cover art for {group['base']}")

    # -- async results
    def _poster_ready(self, key, img, poster_w):
        pix = None
        if img is not None:
            pix = QPixmap.fromImage(img)
            pix.setDevicePixelRatio(self.view.devicePixelRatioF())
        self.cache.put(key, pix, poster_w)
        self.view.viewport().update()
        self.shelf_view.viewport().update()

    def _poster_dropped(self, key):
        if key in self.cache.items:                     # a stale re-render: keep showing the pixmap we have
            self.cache.refreshing.discard(key)
        else:
            self.cache.discard(key)

    def _scanned(self, warnings):
        self.scanning = False
        self.btn_rescan.setEnabled(True)
        self.load_games()
        self.start_ra_sync()
        text = "; ".join(warnings) if warnings else f"Rescan complete — {len(self.all_groups)} games ready"
        self.status.setText(f"{self.recovery_note}  |  {text}" if self.recovery_note else text)
        self.recovery_note = ""

    # -- launching
    def activate(self, group):
        if time.monotonic() - self.last_launch < 0.6:      # swallow double-click repeats
            return
        if len(group["rows"]) == 1:
            self.launch_row(group["rows"][0])
            return
        if len(group["rows"]) > COLLECTION_MIN:            # 91 versions do not fit a pick-list: browse them
            self.open_collection(group)
            return
        dlg = VersionDialog(group, self)
        if self._run_dialog(dlg) and dlg.chosen:
            self.launch_row(dlg.chosen)

    # -- settings
    def open_settings(self):
        tabs = [(p, engine.tab_label(p)) for p in engine.tab_names(self.all_groups) if p]
        dlg = SettingsDialog(self, tabs)
        if self._run_dialog(dlg):
            self.apply_settings(dlg.values())

    def apply_settings(self, new):
        """Save the Settings dialog's values atomically and refresh whatever depends on them, without a restart."""
        old = config.load()

        def differs(key, default=""):
            was, now = old.get(key, default), new[key]
            return (was or "") != now if isinstance(now, str) else was != now

        if not config.update(**new):
            self.status.setText("Could not save settings (config.json is not writable or not a JSON object)")
            return False
        notes = []
        if differs("roms_dir") or differs("media_dir"):                # new library: forget art, look again
            for key in list(self.cache.items):
                self.cache.discard(key)
            self.force_ra = True
            self.trigger_rescan()
            notes.append("rescanning the library")
        if differs("steamgriddb_api_key"):
            covers.reload_api_key()
            for key, (pix, _cost) in list(self.cache.items.items()):   # posters that had no art may find some now
                if pix is None:
                    self.cache.discard(key)
            self.view.viewport().update()
            notes.append("SteamGridDB key updated")
        if differs("ra_username") or differs("ra_api_key"):
            ra_client._failed_credentials = None                       # a corrected key must get a fresh try
            self.force_ra = True
            self.start_ra_sync()
            self.reload()
            notes.append("RetroAchievements " + ("on" if ra_client.enabled() else "off"))
        if differs("discord_rpc", True):
            self.presence.set_enabled(new["discord_rpc"])
            notes.append("Discord status " + ("on" if new["discord_rpc"] else "off"))
        if differs("card_width", None):
            self.zoom_to(new["card_width"])
        self.status.setText("Settings saved" + (": " + ", ".join(notes) if notes else ""))   # the scan's own message follows
        return True

    def open_collection(self, group):
        """The collection browser for a big stack (fresh rows from the DB: playtime, notes, trophies)."""
        try:
            ids = [r["id"] for r in group["rows"]]
            with db.session() as con:
                rows = con.execute(f"SELECT * FROM games WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
        except Exception as e:
            self.status.setText(f"Could not open the collection: {e}")
            return
        dlg = CollectionDialog(group, rows, self)
        dlg.launch_requested.connect(self.launch_row)
        dlg.show_requested.connect(lambda rid, g=group: self._show_version(g, rid))
        self._run_dialog(dlg)

    def _show_version(self, group, row_id):
        """Select the stack in the grid, open the details drawer and show that version."""
        self.set_tab(None)
        self.search.clear()
        for i, g in enumerate(self.model.groups):
            if g["key"] == group["key"]:
                self.view.setCurrentIndex(self.model.index(i))
                self.view.scrollTo(self.model.index(i), QAbstractItemView.PositionAtCenter)
                break
        self.btn_details.setChecked(True)
        self.drawer.set_group(next((g for g in self.model.groups if g["key"] == group["key"]), group))
        self.drawer.select_version(row_id)

    def launch_row(self, row):
        self.last_launch = time.monotonic()
        self.drawer.flush()                                # custom arguments typed a moment ago must apply
        try:
            with db.session() as con:
                row = db.get_game(con, row["id"]) or row   # fresh row: custom_args may have changed since the scan
        except Exception:
            pass
        proc, message = engine.launch(row)                 # sets APPIMAGELAUNCHER_DISABLE=1, fully detached
        self.status.setText(message)
        if proc is not None and row["is_installed"]:         # an install dialog is not a play session
            sid = 0
            try:
                with db.session() as con:
                    sid = db.start_session(con, row["id"], pid=proc.pid, pid_start=engine.proc_start(proc.pid))
            except Exception as e:
                self.status.setText(f"{message} (session not saved: {e})")
            self.sessions.watch(proc, row, sid)
            if ra_client.needs_identify(row):                # hash the ROM now: a trophy badge should be exact
                self.ui_pool.start(RaIdentifyTask(self.signals, row["id"]))
            self.presence.set(engine.presence_title(row), engine.platform_name(row["platform"]), time.time())
            self.presence_game = row["id"]

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            if self.search.text():                          # 1. clear the search
                self.search.clear()
            elif self.btn_details.isChecked():              # 2. close the details drawer
                self.btn_details.setChecked(False)
            else:                                           # 3. quit
                self.close()
        elif e.key() == Qt.Key_Down and self.search.hasFocus():
            self.view.setFocus()
            if not self.view.currentIndex().isValid() and self.model.rowCount():
                self.view.setCurrentIndex(self.model.index(0))
        else:
            super().keyPressEvent(e)

    def closeEvent(self, e):
        """Drop queued work and give running tasks a moment, so quitting never hangs on a slow download."""
        self.epoch[0] += 1
        self.drawer.flush()
        self.presence.shutdown()                          # clears the Discord status and closes the socket
        for pool in (self.pool, self.ui_pool):
            pool.clear()
            pool.waitForDone(1500)
        super().closeEvent(e)


def _arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv[:-1] else default


def main():
    if "--version" in sys.argv:
        print(f"wireplay {__version__}")
        return
    qapp = QApplication(sys.argv)
    qapp.setApplicationName("wireplay")
    qapp.setDesktopFileName("wireplay")                 # matches wireplay.desktop: the window gets its icon / app id
    qapp.setStyleSheet(QSS)
    if "--first-run" in sys.argv:                       # show the setup wizard again (also: "first_run": true in config.json)
        config.update(first_run=True)
    run_first_run_wizard()                              # fresh install: set up before the first scan
    win = Window()
    if tab := _arg("--tab"):
        win.set_tab(tab)
    if query := _arg("--filter"):
        win.search.setText(query)
    win.show()
    if (n := _arg("--select")) is not None and win.model.rowCount():     # test hooks: select a card, open the drawer
        win.view.setCurrentIndex(win.model.index(min(int(n), win.model.rowCount() - 1)))
    if (z := _arg("--zoom")) is not None:                                # test hook: start at a card width
        win.zoom_to(int(z))
    if "--details" in sys.argv:
        win.btn_details.setChecked(True)
    if out := _arg("--screenshot"):                     # test hook: render, wait for posters, save, exit
        def shot():
            win.grab().save(out)
            win.close()
            qapp.quit()
        QTimer.singleShot(int(_arg("--wait", 8000)), shot)
    code = qapp.exec()
    # Pool threads may still be inside a network call (timeouts run to ~25 s) and Qt would join them before exiting,
    # leaving a closed window with a lingering process. Everything they write is atomic (SQLite transactions,
    # temp-file renames), so leave now. The Discord status and unsaved edits were already handled in closeEvent.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


if __name__ == "__main__":
    main()
