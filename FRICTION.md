# Friction notes

Every setup problem, written down the moment it happens.

By the end of the build everything will work on this machine and it will be
impossible to remember what was hard. These notes become the documentation —
they are the difference between setup instructions that work for a stranger
and ones that only work here.

Add to this file whenever anything is confusing, broken, or needs a step that
isn't obvious. Thirty seconds each.

---

## Phase 0

**`python` is not on PATH on this machine; `py` is.** Windows ships a launcher
shortcut that prints a Microsoft Store message instead of running Python. Setup
instructions must not assume `python` works — use `py -m venv` on Windows, and
the venv's own `python.exe` after activation.

**`pip` was bound to an unrelated project's virtualenv** before this project had
its own. Anything installed would have landed in the wrong place. The project
venv has to be created and used explicitly, not assumed.

## Phase 1

**The Windows console cannot print non-ASCII characters by default.** Printing a
box-drawing character crashed `python -m eval --tricky` outright with a
`UnicodeEncodeError`, because the default console encoding is cp1252 rather than
UTF-8. Em dashes in the data file then rendered as `?` even after the crash was
fixed.

The durable fix is to reconfigure stdout and stderr to UTF-8 at the start of
every entry point, with `errors="replace"` so an incapable terminal degrades
instead of crashing. See `_use_utf8_output()` in `eval/__main__.py`.

**Every new entry point needs this**, including the gateway server in Phase 4 and
anything that prints an error message. It is not obvious, it only shows up on
Windows, and the failure looks like a bug in the program rather than in the
terminal.
