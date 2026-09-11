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
