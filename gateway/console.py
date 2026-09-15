"""Helpers for anything that prints to a terminal."""

import contextlib
import sys


def use_utf8_output() -> None:
    """Windows consoles default to cp1252, which mangles or crashes on any
    non-ASCII character. Ask for UTF-8, and fall back to replacing characters
    rather than raising if the terminal genuinely can't manage it.

    Every entry point calls this first. See FRICTION.md.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        # A stream that has already been read from can't change its encoding.
        # Printing still works; it just keeps the encoding it had.
        with contextlib.suppress(ValueError, OSError):
            reconfigure(encoding="utf-8", errors="replace")
