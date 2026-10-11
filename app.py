#!/usr/bin/env python3
"""wireplay - fast, lightweight Linux game launcher.

  python3 app.py           interactive launcher (Tab/[ ] switch platform, type to filter, Enter to launch, Esc to quit)
  python3 app.py --scan    rescan and print counts, no TUI
  python3 app.py --list    print installed games, no TUI
  python3 app.py --version print the version
"""
import curses
import locale
import os
import sys
from pathlib import Path

import covers
import config
import db
from version import __version__
from engine import group_games, launch, scan, tab_label, tab_names   # noqa: F401  (re-exported for callers of app.*)


def draw_tabs(stdscr, tabs, active, w):
    labels = [f"[{tab_label(p)}]" if p is None else f" {tab_label(p)} " for p in tabs]
    widths = [len(l) + 1 for l in labels]
    start = 0                      # scroll the bar so the active tab stays visible
    while start < active and sum(widths[start:active + 1]) > w - 2:
        start += 1
    x = 0
    for i in range(start, len(tabs)):
        if x + len(labels[i]) > w - 1:
            break
        attr = curses.A_REVERSE | curses.A_BOLD if i == active else (curses.A_BOLD if tabs[i] is None else 0)
        stdscr.addnstr(0, x, labels[i], w - 1 - x, attr)
        x += widths[i]


class Pairs:
    """Lazily allocate curses colour pairs for (fg, bg) 256-colour indexes."""

    def __init__(self):
        self.map, self.ok = {}, curses.has_colors()

    def attr(self, fg, bg):
        if not self.ok or (fg, bg) == (-1, -1):
            return 0
        if (fg, bg) not in self.map:
            n = len(self.map) + 1
            if n >= curses.COLOR_PAIRS:
                return 0
            try:
                curses.init_pair(n, fg, bg)
            except curses.error:
                return 0
            self.map[(fg, bg)] = n
        return curses.color_pair(self.map[(fg, bg)])


def put(stdscr, y, x, text, width, attr=0):
    """addnstr that ignores the bottom-right-corner error and out-of-range writes."""
    if width <= 0:
        return
    try:
        stdscr.addnstr(y, x, text, width, attr)
    except curses.error:
        pass


def clip(text, width, left=False):
    if len(text) <= width:
        return text
    return ("\u2026" + text[-(width - 1):]) if left else (text[:width - 1] + "\u2026")


def draw_details(stdscr, pairs, x0, rw, h, group, row=None):
    """Right pane: title, platform badge, box art (chafa) or placeholder, path/AppID and version info."""
    rows = [row] if row else group["rows"]
    first = rows[0]
    steam = first["platform"] == "steam"
    cover = covers.find_cover(rows)
    fetching = None
    if not cover:
        covers.start_fetch(rows)
        fetching = covers.fetch_state(rows)

    put(stdscr, 0, x0, clip(row["title"] if row else group["base"], rw), rw, curses.A_BOLD)
    badge = f" {'Steam' if steam else first['platform']} "
    put(stdscr, 1, x0, badge, rw, curses.A_REVERSE)
    if not row and len(group["rows"]) > 1:
        put(stdscr, 1, x0 + len(badge) + 1, f"{len(group['rows'])} versions", rw - len(badge) - 1, curses.A_DIM)

    detail_n = 4
    ih = max(3, h - 3 - detail_n - 1)
    aw = rw
    art = None
    if cover:
        bw, bh = covers.box_size(cover, aw, ih)
        art = covers.render(str(cover), bw, bh)
    if art:
        y_off = max(0, (ih - len(art)) // 2)                # centre vertically as well as horizontally
        for i, segs in enumerate(art[:ih]):
            x = x0 + max(0, (aw - sum(len(t) for t, _, _ in segs)) // 2)
            for text, fg, bg in segs:
                put(stdscr, 3 + y_off + i, x, text, x0 + rw - x, pairs.attr(fg, bg))
                x += len(text)
    else:
        msg = "fetching cover\u2026" if fetching == "pending" else "no cover"
        bw = min(aw, 36)
        bx = x0 + (aw - bw) // 2
        put(stdscr, 3, bx, "\u250c" + "\u2500" * (bw - 2) + "\u2510", bw, curses.A_DIM)
        for i in range(1, ih - 1):
            put(stdscr, 3 + i, bx, "\u2502" + " " * (bw - 2) + "\u2502", bw, curses.A_DIM)
        put(stdscr, 3 + ih - 1, bx, "\u2514" + "\u2500" * (bw - 2) + "\u2518", bw, curses.A_DIM)
        put(stdscr, 3 + ih // 2, bx + 1, msg.center(bw - 2), bw - 2, curses.A_DIM)

    y = 3 + ih + 1
    if steam:
        put(stdscr, y, x0, f"AppID: {first['path_or_id']}", rw)
    else:
        p = Path(first["path_or_id"])
        put(stdscr, y, x0, clip(f"File: {p.name}", rw), rw)
        put(stdscr, y + 1, x0, clip(f"Dir:  {p.parent}", rw, left=True), rw, curses.A_DIM)
    if row:
        put(stdscr, y + 2, x0, clip(f"Version: {row['version_label']}", rw), rw)
    else:
        put(stdscr, y + 2, x0, f"Versions: {len(group['rows'])}", rw)


def tui(stdscr, games, warnings):
    curses.curs_set(1)
    stdscr.keypad(True)
    try:
        curses.start_color()
        curses.use_default_colors()
    except curses.error:
        pass
    pairs = Pairs()
    groups = group_games(games)
    tabs = tab_names(groups)
    tab, query, sel, top, status = 0, "", 0, 0, "; ".join(warnings)
    sub = None                     # the group whose version sub-menu is open
    sub_sel = 0

    while True:
        h, w = stdscr.getmaxyx()
        split = w >= 90            # too narrow for two columns: list only
        lw = int(w * 0.6) if split else w
        rows = max(1, h - 4)
        stdscr.erase()
        draw_tabs(stdscr, tabs, tab, lw - 1 if split else w)
        if split:
            for y in range(h):
                put(stdscr, y, lw - 1, "\u2502", 1, curses.A_DIM)
        x0, rw = lw + 1, w - lw - 2
        lw_text = lw - 1 if split else w          # usable width of the left column

        if sub:
            items = sub["labels"]
            sub_sel = max(0, min(sub_sel, len(items) - 1))
            put(stdscr, 1, 0, f" {sub['base']}  [{sub['platform']}]", lw_text - 1, curses.A_BOLD)
            s_top = min(max(0, sub_sel - (rows - 1) + 1), sub_sel)
            for i, label in enumerate(items[s_top:s_top + rows - 1]):
                attr = curses.A_REVERSE if s_top + i == sub_sel else 0
                put(stdscr, 2 + i, 0, f"   {clip(label, lw_text - 4):<{max(1, lw_text - 4)}}", lw_text - 1, attr)
            put(stdscr, h - 2, 0, "Enter: launch version   Esc: back", lw_text - 1, curses.A_DIM)
            stdscr.move(h - 1, 0)
            if split:
                draw_details(stdscr, pairs, x0, rw, h, sub, sub["rows"][sub_sel])
        else:
            active = tabs[tab]
            terms = query.lower().split()
            hits = [g for g in groups
                    if (active is None or g["platform"] == active) and all(t in g["haystack"] for t in terms)]
            sel = max(0, min(sel, len(hits) - 1))
            top = min(max(top, sel - rows + 1), sel) if hits else 0
            plat_w = min(12, max(4, lw_text // 8))
            tw = max(1, lw_text - plat_w - 4)
            for i, g in enumerate(hits[top:top + rows]):
                attr = curses.A_REVERSE if top + i == sel else 0
                put(stdscr, 1 + i, 0, f" {g['text']:<{tw}.{tw}} {g['platform']:>{plat_w}.{plat_w}} ", lw_text - 1, attr)
            put(stdscr, h - 2, 0, status, lw_text - 1, curses.A_DIM)
            prompt = f"> {query}"
            put(stdscr, h - 1, 0, prompt, lw_text - 1)
            count = f"{len(hits)} games"
            if lw_text > len(prompt) + len(count) + 3:
                put(stdscr, h - 1, lw_text - len(count) - 1, count, len(count), curses.A_DIM)
            if split and hits:
                draw_details(stdscr, pairs, x0, rw, h, hits[sel])
            stdscr.move(h - 1, min(len(prompt), lw_text - 1))
        stdscr.refresh()

        stdscr.timeout(120 if covers.any_pending() else -1)   # poll while a cover downloads
        try:
            k = stdscr.get_wch()
        except curses.error:
            continue
        if sub:
            if k == "\x1b":
                sub = None
            elif k in ("\n", "\r") or k == curses.KEY_ENTER:
                _, status = launch(sub["rows"][sub_sel])
                sub = None
            elif k == curses.KEY_UP:
                sub_sel -= 1
            elif k == curses.KEY_DOWN:
                sub_sel += 1
            continue
        if k == "\x1b":                               # Esc: clear query, else quit
            if not query:
                return
            query, sel, top = "", 0, 0
        elif k in ("\n", "\r") or k == curses.KEY_ENTER:
            if hits:
                g = hits[sel]
                if len(g["rows"]) == 1:
                    _, status = launch(g["rows"][0])
                else:
                    sub, sub_sel = g, 0
        elif k in ("\t", "]"):
            tab, sel, top = (tab + 1) % len(tabs), 0, 0
        elif k in ("[", curses.KEY_BTAB):
            tab, sel, top = (tab - 1) % len(tabs), 0, 0
        elif k == curses.KEY_UP:
            sel -= 1
        elif k == curses.KEY_DOWN:
            sel += 1
        elif k == curses.KEY_PPAGE:
            sel -= rows
        elif k == curses.KEY_NPAGE:
            sel += rows
        elif k == curses.KEY_HOME:
            sel = 0
        elif k == curses.KEY_END:
            sel = len(hits) - 1
        elif k in (curses.KEY_BACKSPACE, "\x7f", "\b"):
            query, sel, top = query[:-1], 0, 0
        elif k == "\x15":                             # Ctrl-U
            query, sel, top = "", 0, 0
        elif isinstance(k, str) and k.isprintable():
            query, sel, top = query + k, 0, 0


def main():
    if "--version" in sys.argv:
        print(f"wireplay {__version__}")
        return
    config.ensure_paths()                      # remember the detected ROM / art folders in an existing config.json
    with db.session() as con:
        warnings = scan(con)
        games = db.installed(con)
        c = db.counts(con)
    if "--scan" in sys.argv:
        steam, minecraft = c.pop("steam", 0), c.pop("minecraft", 0)
        print(f"Steam: {steam}\nMinecraft: {minecraft}\nROMs:  {sum(c.values())}")
        for plat, n in c.items():
            print(f"  {plat:<12}{n}")
        for w in warnings:
            print("warning:", w)
        return
    if "--list" in sys.argv:
        for g in games:
            print(f"{g['platform']:<10} {g['title']}")
        return
    locale.setlocale(locale.LC_ALL, "")
    os.environ.setdefault("ESCDELAY", "25")
    try:
        curses.wrapper(tui, games, warnings)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
