# Friction notes

The notebook for this build. One entry per problem, four short lines each:
what broke, why, the fix, what it taught.

Skim the headings. Stop at anything that looks interesting. Nothing here needs
reading start to finish.

Each entry ends with **In my words** — the same bug explained in my own words.
That line is the one that proves I understood it, so nobody edits it but me.

Nothing is left out for being embarrassing. The wrong turns are worth the most.

---

## Phase 0 — setup

### `python` is not on PATH on this machine

**Broke** — `python` printed a Microsoft Store advert instead of running.

**Cause** — Windows ships a launcher shortcut, not Python. `py` is the real one.

**Fix** — `py -m venv .venv`, then the venv's own `python.exe` after that.

**Lesson** — setup instructions must never assume `python` works on Windows.

### `pip` was installing into another project's virtualenv

**Broke** — packages would have landed in an unrelated project (`Base_Ecom`).

**Cause** — no venv existed here yet, so `pip` used whichever was active.

**Fix** — create this project's venv first, and call it explicitly.

**Lesson** — "which python am I actually running" is the first question, always.

---

## Phase 1 — the evaluation set

### The Windows console cannot print non-ASCII by default

**Broke** — `python -m eval --tricky` crashed with `UnicodeEncodeError` on a
box-drawing character. Em dashes then showed as `?`.

**Cause** — the console's default encoding is cp1252, not UTF-8.

**Fix** — `use_utf8_output()` in `gateway/console.py`, called at the start of
every entry point, with `errors="replace"` so a weak terminal degrades instead
of crashing. It started inside `eval/__main__.py` and moved out in Phase 3.

**Lesson** — every new entry point needs that call, including the Phase 4
server. The failure looks like a bug in the program, but it's the terminal.

---

## Phase 2 — the redaction engine

### The test data was wrong, not the code

**Broke** — the PAN test failed on `ABCDE1234F`.

**Cause** — the data. A PAN's 4th character encodes the holder type, and `D`
isn't one of them, so that string can't exist. The validator was right.

**Fix** — corrected the case to `ABCPE1234F` (`P` = individual).

**Lesson** — when a check fails a case, the case might be the wrong one. Verify
the expected answer before changing code to produce it.

**In my words** —

### The card prefix rule rejected real cards

**Broke** — `2223 0031 2200 3222`, a published Mastercard test number, was
rejected. So was every RuPay card.

**Cause** — the rule assumed cards start with 3, 4, 5 or 6. Mastercard was
given the 2221–2720 range in 2017, and RuPay uses 81–82.

**Fix** — an exact range check plus 81 and 82, in `looks_like_card()`. Two
boundary cases guard it: `2220999999999991` and `2721000000000004`, one either
side, both valid by Luhn, so only the range keeps them out.

**Lesson** — a rule remembered from memory is frozen at the date you learned
it. Check the real range, and test its edges: the middle always passes.

**In my words** —

### Context words were matching inside other words

**Broke** — three ordinary sentences were being redacted:

    our company code is ABCPE1234F      "pan" inside "company"
    follow the guide, section 2345 6789 0123     "uid" inside "guide"
    after the data breach 9876543210 records leaked    "reach" inside "breach"

**Cause** — the context check was a plain substring search.

**Fix** — word boundaries around every context word: `\b(?:pan|pancard|…)\b`,
in `_c()` in `patterns.py`. All three are now permanent negative cases.

**Lesson** — this kind of false positive is invisible to tests that only check
what gets *caught*. You find it by writing sentences that must be left alone.

**In my words** —

### The holdout found a category that didn't exist

**Broke** — UPI IDs went straight through. The main set was passing 100%.

**Cause** — `ravi@ybl` is not an email (no dot after the `@`), so the email
pattern ignored it, and nothing else was looking. There was no UPI pattern at
all — not a weak one, none.

**Fix** — UPI as its own category, recognised three ways without anyone writing
the word "upi": a known handle (`@ybl`, `@okaxis`), a name that is a 10-digit
mobile number, or a handle naming a bank (`@axisb`, `@hdfcpay`). Only the name
is hidden — `[UPI_NAME_1]@axis` — because the handle isn't personal and is
usually what the question is about.

**Lesson** — the ten cases that found this were written independently, without
looking at the patterns. Our own 110 could never have found it: you can only
test for something you've thought of. DECISIONS.md had said a holdout wasn't
needed for regex, since there's no score to fool yourself with — right
reasoning, wrong conclusion. The risk with regex is a **blind spot**, and a
blind spot is invisible from the inside. The doc is corrected, and every phase
now closes with freshly written cases.

**In my words** —

### A decision written down twice still didn't happen

**Broke** — the patterns were never measured against Microsoft Presidio, though
DECISIONS.md promised it twice: in the tech stack, and in the Phase 2 plan.

**Cause** — Phase 2's exit criteria said only "every synthetic case caught,
nothing in the negative set falsely flagged". Those were met, so the phase
closed. Nothing was decided and nothing was dropped; it just went unchecked.

**Fix** — pending: either run the comparison, or record in DECISIONS.md that
it's dropped, and why.

**Lesson** — a plan is enforced only where it is *checked*. Intentions in the
prose above a phase are checked by nothing. If it matters, it goes in the exit
criteria, where the phase can't close without it.

### Unfinished: the Aadhaar checksum is written but switched off

**What** — real Aadhaar numbers satisfy a Verhoeff checksum, and
`passes_verhoeff()` implements it. It isn't wired into the pattern.

**Why not** — our test numbers are invented, and invented numbers fail a real
checksum. Turning it on would fail our own positive cases, proving nothing.

**To finish** — generate Verhoeff-valid synthetic numbers, then add the
validator. The pattern could then drop its context requirement and catch a bare
Aadhaar number with no surrounding words, which today it cannot.

### Accepted trade: three categories can't be identified by shape alone

**What** — bare phone numbers, Aadhaar and PAN need a nearby word to count.

**Why** — `9876543210` is a mobile number in one sentence and an invoice
reference in another. Identical digits. Nothing in the shape decides it.

**The trade** — a value pasted with no words around it is missed. Accepted:
missing one is better than redacting every invoice number in the company.

---

## Phase 3 — pipeline and config

### `max()` returned the weaker action

**Broke** — `max([REDACT, BLOCK])` returned REDACT. A request that should have
been blocked would have been redacted and sent onward.

**Cause** — `Action` is an Enum *and* a `str`, and only `__lt__` was defined.
`max()` compares with `>`, which fell back to `str`'s `>` and compared the words
alphabetically. "redact" sorts after "block".

**Fix** — all four comparisons written out, all following severity. (Python
fills in the rest only if you ask, with `@functools.total_ordering`.)

**Lesson** — subclass `str` and every comparison you don't define silently
means something else. A half-defined protocol is worse than none: it fails
quietly, in the direction of doing less. Nothing called `max()` before the
pipeline existed, so no earlier result was wrong — the bug sat in the file since
Phase 2, waiting for its first caller.

**In my words** —

### YAML reads a bare `off` as the boolean false

**Broke** — `phone: off` in rules.yaml would have been rejected with a
confusing message about booleans.

**Cause** — YAML turns `off`, `on`, `no` and `yes` into booleans. Python
receives `False`, never the word.

**Fix** — the rules loader turns `false` back into `off`. A test writes `off`
in real YAML to keep it that way.

**Lesson** — anyone adding a setting that takes words must remember this.
Quoting (`phone: "off"`) also works, but no operator should have to know that.

**In my words** —

### Whole words fixed one bug and caused another

**Broke** — after context words had to match as whole words, "he called from
9876543210 yesterday" stopped being caught. So did "contacted", "callback",
"adhar", "watsapp", "mob".

**Cause** — "called" is not "call". Exact whole-word matching lost every
ending people actually write.

**Fix** — context words carry their endings: `call\w*`, `phone\w*`, `aadha\w*`,
`wh?ats?ap\w*`. The word boundary stays at the front, so "reach" still can't
match inside "breach". "mob" stays exact — mobility and mobster say nothing
about a phone.

**Lesson** — **a typo makes this gateway miss, never over-redact.** Detection
keys on the words around a value, so a misspelled word means no match and the
text passes through untouched. Failing towards "do nothing" is the right
direction for something sitting in everyone's traffic. It surfaced while
answering a question: the claim was that deleting `phone: off` would redact a
sentence. It didn't — the sentence said "called". Running the command proved the
explanation wrong, not the code.

**In my words** —

### A flag that silently answered a different question

**Broke** — `python -m eval --holdout --score` printed the **main set's** score.
It looked like a pass.

**Cause** — `--score` was checked first and returned, so `--holdout` was never
read. The only tell was the title line above the numbers.

**Fix** — the flags are read together: `--score` is what to do, `--holdout` is
which cases to do it on. Three tests cover the combinations.

**Lesson** — in an evaluation tool, a wrong answer that looks like a right one
is the worst possible bug, and the holdout is the worst place to have it. Flags
that answer different questions can't be a chain of early returns.

**In my words** —

---

## Before the first push

### The repo looked like it had leaked API keys

**Broke** — nothing, in security terms. Every key in the evaluation set was
invented and opens nothing. But `sk-abc123XYZdef456…` reads as a live key.

**Cause** — realistic test data written *too* realistically. Two real costs:
GitHub's push protection can refuse a push over a string that merely looks
live, and a stranger skimming a security project assumes the author leaked
their own keys.

**Fix** — they now say what they are: `sk-not-a-real-key-000…`,
`ghp_notarealkey0…`, keeping the prefix and length the patterns match on, so
detection is unchanged. AWS's two values stayed — they are Amazon's published
examples and already say EXAMPLE. So did the JWT (payload `{"sub":"1"}`) and
the PEM line, a header with no key after it.

**Lesson** — in a security project, looking like a leak costs almost as much as
being one.

**In my words** —

### Three fixes had to happen in the history, not just the files

**Broke** — old commits still showed the realistic keys, a personal gmail
address on every commit, and an assistant's `Co-Authored-By` trailer.

**Cause** — editing a file today doesn't change what past commits show, and
both people and scanners read the whole history.

**Fix** — one `git filter-branch` pass over all 14 commits did all three at
once. Verified **before** pushing: one email address in the entire history,
zero occurrences of the old key strings in any commit, zero attribution lines,
tests still passing on the rewritten tree, and a backup bundle taken first.

**Lesson** — **git history is published data, and a commit is not a draft.**
This rewrite is cheap exactly once: after the first push, the same fix means
force-pushing over what other people already pulled.

**In my words** —
