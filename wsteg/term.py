# SPDX-License-Identifier: GPL-3.0-or-later
"""ANSI colours for the command line, switched off when the output isn't a terminal."""
from __future__ import annotations

import os
import sys

CODES = {
    "bold": "1", "dim": "2", "underline": "4",
    "red": "31", "green": "32", "yellow": "33", "blue": "34", "magenta": "35", "cyan": "36",
    "bright_green": "92", "bright_yellow": "93", "bright_cyan": "96", "bright_white": "97",
}


def _enable_windows_vt(stream) -> bool:
    """Turn on ANSI escape handling in a Windows console (Windows 10+)."""
    try:
        import ctypes
        import msvcrt

        k32 = ctypes.windll.kernel32
        handle = msvcrt.get_osfhandle(stream.fileno())
        mode = ctypes.c_uint32()
        if not k32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(k32.SetConsoleMode(handle, mode.value | 0x0004))  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        return False


def use_color(mode: str = "auto", stream=None) -> bool:
    """'always' / 'never', or for 'auto': a terminal, no NO_COLOR, and not TERM=dumb."""
    stream = sys.stdout if stream is None else stream
    if mode == "never":
        return False
    if mode == "auto":
        if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
            return False
        try:
            if not stream.isatty():
                return False
        except (AttributeError, ValueError):
            return False
    if sys.platform == "win32":
        _enable_windows_vt(stream)  # best effort; 'always' still emits codes
    return True


class Painter:
    """`p(text, "bold", "green")` wraps text in ANSI codes when enabled, else returns it unchanged."""

    def __init__(self, enabled: bool):
        self.enabled = enabled

    def __call__(self, text: str, *styles: str) -> str:
        if not self.enabled or not styles or not text:
            return text
        return f"\x1b[{';'.join(CODES[s] for s in styles)}m{text}\x1b[0m"
