"""Full-screen colour dashboard for `superfan status --watch`.

A thin drawing layer: all content comes from status.load_frame, which only
reads the status file the daemon publishes.
"""

from __future__ import annotations

import curses
import sys
import time
from typing import Dict

from .status import Frame, load_frame

FOOTER = "Press q to quit"


def _styles() -> Dict[str, int]:
    styles = {"": curses.A_NORMAL, "bold": curses.A_BOLD, "title": curses.A_BOLD, "dim": curses.A_DIM,
              "ok": curses.A_NORMAL, "warn": curses.A_BOLD, "bad": curses.A_BOLD | curses.A_REVERSE}
    if curses.has_colors():
        background = curses.COLOR_BLACK
        try:
            curses.use_default_colors()
            background = -1
        except curses.error:
            pass
        for number, colour in ((1, curses.COLOR_GREEN), (2, curses.COLOR_YELLOW), (3, curses.COLOR_RED),
                               (4, curses.COLOR_CYAN)):
            curses.init_pair(number, colour, background)
        styles.update(ok=curses.color_pair(1), warn=curses.color_pair(2) | curses.A_BOLD,
                      bad=curses.color_pair(3) | curses.A_BOLD, title=curses.color_pair(4) | curses.A_BOLD)
    return styles


def draw(screen, frame: Frame, styles: Dict[str, int]) -> None:
    screen.erase()
    height, width = screen.getmaxyx()
    for y, row in enumerate(frame[:height]):
        x = 0
        for text, style in row:
            room = width - 1 - x
            if room <= 0:
                break
            try:
                screen.addnstr(y, x, text, room, styles.get(style, curses.A_NORMAL))
            except curses.error:
                pass  # the terminal shrank under us; the next refresh redraws
            x += len(text)
    screen.refresh()


def _loop(screen, status_file: str, refresh: float) -> int:
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    styles = _styles()
    screen.timeout(int(refresh * 1000))
    while True:
        frame, _ = load_frame(status_file, time.time(), footer=FOOTER)
        draw(screen, frame, styles)
        key = screen.getch()
        if key in (ord("q"), ord("Q"), 27):
            return 0


def watch(status_file: str, refresh: float = 1.0) -> int:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("--watch needs a terminal; use `superfan status` for plain output", file=sys.stderr)
        return 2
    try:
        return curses.wrapper(_loop, status_file, refresh)
    except KeyboardInterrupt:
        return 0
    except curses.error as exc:
        print("cannot draw the dashboard on this terminal (%s); use `superfan status`" % exc, file=sys.stderr)
        return 2
