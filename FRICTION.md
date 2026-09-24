# Friction notes

The notebook for this build. Every problem, in the order it happened: what broke,
why it broke, how it was found, what fixed it, and what it taught.

Two reasons this file exists.

**Documentation.** By the end everything will work on this machine and it will be
impossible to remember what was hard. These notes are the difference between setup
instructions that work for a stranger and ones that only work here.

**Understanding.** A project you cannot explain is not yours. Each entry is written
to be read back cold, months later, and explained out loud to someone else — what
the bug was, why the obvious version of the code was wrong, and how it was proved
rather than guessed.

Nothing is left out for being embarrassing. The wrong turns are the entries worth
the most: a bug that was merely fixed teaches nothing, a bug that was *understood*
is the one worth talking about.

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
instead of crashing. See `use_utf8_output()` in `gateway/console.py` — it started
in `eval/__main__.py` and moved out in Phase 3 so every entry point shares one
copy.

**Every new entry point needs this**, including the gateway server in Phase 4 and
anything that prints an error message. It is not obvious, it only shows up on
Windows, and the failure looks like a bug in the program rather than in the
terminal.

## Phase 2

**The test data was wrong, not the code.** The PAN positives used `ABCDE1234F`,
which is structurally impossible — the fourth character of a real PAN encodes the
holder type, and `D` is not one of them. The validator correctly rejected it and
the test correctly failed. Fixed the data.

Worth remembering: when a check fails a case, the case might be the thing that's
wrong. Verify the expected answer before changing the code to produce it.

**The card prefix rule rejected real cards.** Luhn alone isn't enough — ordinary
reference numbers pass it by chance — so a card must also *start* like a card.
The first version of that rule assumed cards begin with 3, 4, 5 or 6. It rejected
`2223 0031 2200 3222`, a published Mastercard test number, and every RuPay card,
which India uses and which begins 81 or 82.

Mastercard was given the 2221–2720 range in 2017. That is why the old rule looks
right and isn't: it is a rule remembered from an earlier decade. The fix is an
exact range check rather than a first-digit check, plus 81 and 82. Two boundary
cases now guard it — `2220999999999991` and `2721000000000004`, one either side
of the range, both valid by Luhn, so only the range keeps them out. See
`looks_like_card()` in `gateway/engine/checks/validators.py`.

The lesson: a rule copied from memory is frozen at the date you learned it. Check
the real range, and test the edges of it, because the middle always passes.

**Context words were matching inside other words.** Three ordinary sentences were
being redacted:

    our company code is ABCPE1234F          "pan" hiding inside "company"
    follow the guide, section 2345 6789     "uid" hiding inside "guide"
    after the data breach 9876543210        "reach" hiding inside "breach"

The context check was a plain substring search. The fix wraps every context word
in word boundaries — `\b(?:pan|pancard|…)\b`, in `_c()` in
`gateway/engine/checks/patterns.py` — so a word only counts when it stands alone.
All three sentences are now permanent cases in `eval/data/pii_negative.yaml`.

The lesson: this kind of false positive is invisible to a test suite that only
checks what gets *caught*. It appears only if you deliberately write sentences
that must be left alone — which is why the negative file is the more important
of the two.

**Aadhaar checksum validation is written but not switched on.** Real Aadhaar
numbers satisfy a Verhoeff checksum, and `passes_verhoeff()` implements it.
It isn't wired into the pattern because our test numbers are invented, and
invented numbers fail a real checksum — turning it on would fail our own
positive cases rather than prove anything.

**To finish this:** generate Verhoeff-valid synthetic numbers for the evaluation
set, then add the validator to the Aadhaar pattern. That would let the pattern
drop its context requirement and catch a bare Aadhaar number with no
surrounding words, which it currently cannot.

**Three categories cannot be identified by shape alone** — bare phone numbers,
Aadhaar, and PAN. `9876543210` is a mobile number in one sentence and an invoice
reference in another, and they are the same ten digits. These patterns require a
nearby word, which means a value pasted with no context around it is missed. That
is a deliberate trade: missing one is better than redacting every invoice number
in the company.

**The holdout found a whole category that didn't exist.** The main evaluation set
was passing 100%. Then ten cases written independently — written without looking
at what the detection could do — went through, and UPI IDs sailed straight past.
`ravi@ybl` is not an email, because there is no dot after the `@`, so the email
pattern ignored it. Nothing else was looking for it. The category simply did not
exist.

No amount of work on the tuned set would have revealed this, because the tuned
set contained no UPI IDs. **A set written while building the thing can only tell
you whether it still does what you already thought of.**

UPI became its own category, recognisable three ways without anyone writing the
word "upi": a known handle (`@ybl`, `@okaxis`), a name that is a ten-digit mobile
number, or a handle that names a bank (`@axisb`, `@hdfcpay`). Only the name is
hidden — `[UPI_NAME_1]@axis` — because the handle is not personal, and it is
usually the part the question is about: an assistant can only answer "@axis isn't
a real handle" if it can see `@axis`.

The discipline that came out of it, and which holds for the rest of the build:
keep writing cases the detection has never seen, and count over-redaction as a
failure, not a curiosity.

The plan had said this wasn't necessary here. DECISIONS.md argued that regex
needs no holdout, because a pattern either matches or it doesn't and there is no
score to fool yourself with. The reasoning was right and the conclusion was
wrong: the risk with regex isn't a flattering score, it's a **blind spot** — and
a blind spot is invisible from the inside by definition. The doc has been
corrected, and closing a phase with freshly written cases is now a working
practice rather than a lucky habit.

**A decision written down twice still didn't happen.** DECISIONS.md said the
patterns would be measured against Microsoft Presidio — once in the tech-stack
section, once in the Phase 2 plan. Phase 2 closed without it. Nothing was
decided and nothing was dropped: the phase's exit criteria said "every synthetic
case caught, and nothing in the negative set falsely flagged", those were met,
and the comparison was never looked at again. It surfaced later, only by reading
DECISIONS.md and these notes side by side.

The lesson: a plan is enforced only where it is *checked*. Intentions written in
the prose above a phase are not checked by anything. If something matters, it
belongs in that phase's exit criteria, where the phase cannot close without it.

## Phase 3

**`max()` returned the weaker action.** The pipeline takes the strongest action
any check asked for, on the order `allow < log < redact < block`. `Action` is an
`Enum` that is also a `str`, and only `__lt__` was defined. `max()` compares with
`>` — which was never defined — so Python fell back to `str`'s own `>` and
compared the words *alphabetically*. "redact" sorts after "block", so
`max([REDACT, BLOCK])` returned **REDACT**: a request that should have been
blocked would have been redacted and sent onward.

Defining `__lt__` alone is enough for `sorted()`, which is why the code looks
correct and passes a reading. Python fills in the rest only if you ask, with
`@functools.total_ordering`. All four comparisons are now written out and all
four follow severity. Nothing called `max()` before the pipeline existed, so no
earlier result was wrong — but the bug had been sitting in the file since
Phase 2, waiting for its first caller.

The lesson: subclassing `str` means every comparison you *don't* define silently
means something else. A half-implemented protocol is worse than none, because it
fails quietly, and it fails in the direction of doing less.

**YAML reads a bare `off` as the boolean false.** `phone: off` in rules.yaml arrives
in Python as `phone: False`, not the word "off" — and `no`, `yes` and `on` are
turned into booleans the same way. Left alone, the most natural thing an operator
could write would be rejected with a confusing message about booleans. The rules
loader turns `false` back into `off`, and a test writes `off` in real YAML to keep
it that way.

Anyone adding a setting that takes words must remember this. Quoting the value
(`phone: "off"`) also avoids it, but no operator should have to know that.

**Whole words fixed one bug and introduced another.** Once context words had to
match as whole words, "he called from 9876543210 yesterday" stopped being caught:
"called" is not "call". Neither were "contacted", "callback", "adhar", "watsapp"
or "mob".

It surfaced while answering a question about the rules file. The claim was:
delete `phone: off` and this sentence gets redacted. It didn't. The sentence said
"called". Running the command proved the *explanation* wrong — a reminder that an
explanation of what code does is a claim, and claims get tested like anything
else.

The fix lets context words carry their endings — `call\w*`, `phone\w*`,
`aadha\w*`, `wh?ats?ap\w*` — while keeping the word boundary at the front, so
"reach" still cannot match inside "breach". "mob" stays exact on purpose:
mobility, mobster and mobilise say nothing about a phone.

Underneath it is the answer to a question worth being able to give: **a typo makes
this gateway miss, never over-redact.** Detection keys on the words around a value,
so a misspelled word means no match and the text passes through untouched. Failing
towards "do nothing" is the right direction for something sitting in the middle of
everyone's traffic.

**A flag that silently answered a different question.** `python -m eval --holdout
--score` printed the main set's score, because `--score` was checked first and
returned before `--holdout` was ever read. The command looked like it passed;
the numbers were from the set the detection was tuned against. The only tell was
the title line above them.

This is the worst shape a bug can take in an evaluation tool — a wrong answer
that looks like a right one, on the one set that exists to be untuned. Flags that
answer different questions (*what to do* versus *which cases to do it on*) have to
be read together, not as a chain of early returns.

## Before the first push

**The repo looked like it had leaked API keys.** The evaluation set needs
realistic keys, and they were written realistically: `sk-abc123XYZdef456…`,
`ghp_abc123…`, a Slack token. Every one invented, none of them opening anything.
Two real costs anyway: GitHub's push protection can refuse a push over a string
that merely *looks* live, and a stranger skimming a security project sees `sk-…`
in the files and assumes the author leaked their own keys.

They now say what they are — `sk-not-a-real-key-000…`, `ghp_notarealkey0…` —
keeping the prefix and length the patterns match on, so detection is unchanged.
AWS's two values stayed: they are Amazon's own published examples and already
say EXAMPLE. So did the JWT, whose payload decodes to `{"sub":"1"}`, and the PEM
line, which is a header with no key after it.

**Three fixes had to happen in the history, not just in the files.** Editing a
file today does not change what old commits show, and both people and scanners
read the whole history. One `git filter-branch` pass over all 14 commits did
three things at once: replaced the key strings everywhere they had ever
appeared, swapped the personal gmail address for the GitHub `noreply` address,
and removed the assistant's `Co-Authored-By` trailer.

Verified *before* pushing, not after: one email address in the entire history,
zero occurrences of the old key strings across every commit, zero attribution
lines, tests still passing on the rewritten tree, and a backup bundle taken
first in case any of it went wrong.

This is cheap exactly once. After the first push the same fix means force-pushing
over what other people have already pulled.

The lesson: **git history is published data.** A commit is not a draft. The
moment to decide what belongs in it is before a remote exists — which, for this
repo, was the afternoon of the first push.
