# Our patterns vs Microsoft Presidio

Run on 27 September 2026, after Phase 3. Reproduce with:

    .venv/Scripts/python -m pip install presidio-analyzer
    .venv/Scripts/python -m spacy download en_core_web_lg
    .venv/Scripts/python -m eval.compare_presidio

DECISIONS.md said twice that the patterns would be measured against Presidio
rather than assumed better than it. This is that measurement.

## Method

The same cases through both detectors: 61 positive, 49 negative, 10 holdout.

Presidio is run in **two configurations**, because one would misrepresent it:

- **presidio** — exactly what `AnalyzerEngine()` gives you out of the box.
- **+india** — the same, plus `InPanRecognizer` and `InAadhaarRecognizer`, which
  ship in the package but are *not* loaded by default, and a phone recognizer
  told that numbers are Indian.

Presidio returns a confidence with every match; anything below **0.5** is
ignored here. Pattern matches score 0.5 and checksum-validated ones score
higher, so this keeps its real answers and drops its guesses.

Our detector is scored **strictly** in every column: it must name the category
the case expects. Presidio gets a strict score and also a **lenient** one, where
flagging anything at all in the text counts, whatever it called it.

## Result

|                | caught  | passed clean |
| -------------- | ------- | ------------ |
| **ours**       | 61 / 61 | 49 / 49      |
| presidio       | 16 / 61 | 48 / 49      |
| +india         | 20 / 61 | 45 / 49      |
| +india lenient | 37 / 61 | 32 / 49      |

By category, on the cases that must be caught:

| category        | ours    | presidio | +india  | lenient |
| --------------- | ------- | -------- | ------- | ------- |
| credit_card     | 9 / 9   | 7 / 9    | 7 / 9   | 7 / 9   |
| email           | 4 / 4   | 4 / 4    | 4 / 4   | 4 / 4   |
| ip_address      | 2 / 2   | 2 / 2    | 2 / 2   | 2 / 2   |
| pan             | 4 / 4   | 0 / 4    | 4 / 4   | 4 / 4   |
| aadhaar         | 4 / 4   | 0 / 4    | 0 / 4   | 2 / 4   |
| phone           | 11 / 11 | 1 / 11   | 1 / 11  | 7 / 11  |
| api_key         | 9 / 9   | 0 / 9    | 0 / 9   | 3 / 9   |
| password        | 1 / 1   | 0 / 1    | 0 / 1   | 0 / 1   |
| upi_id          | 15 / 15 | 0 / 15   | 0 / 15  | 6 / 15  |
| several at once | 2 / 2   | 2 / 2    | 2 / 2   | 2 / 2   |

Holdout: ours 6/6 and 4/4 clean; Presidio 1/6 out of the box, 2/6 with the
Indian recognizers, 4/4 clean.

## What those numbers actually mean

A total of 61 against 16 flatters us, and the reasons matter more than the
score. Taken honestly, in four groups:

### Where we only barely win, and the reason is interesting

**Cards: 9/9 against 7/9.** The two Presidio misses are
`2223 0031 2200 3222` and `8150 0000 0000 1231` — a published Mastercard test
number from the 2-series, and a RuPay number.

Those are the *exact two* our own first prefix rule rejected, for the same
reason: a card-prefix list written before Mastercard was given the 2221–2720
range in 2017, and without India's RuPay in it. We found that bug in Phase 2
against our own test data. Microsoft's library still has it.

**Email and IP: identical.** 4/4 and 2/2 both ways. On plain, well-specified
patterns there is nothing to choose between us — as expected.

### Where the comparison says nothing, because Presidio isn't playing

**API keys 0/9, passwords 0/1.** Presidio has no concept of either. It is a PII
library, not a secrets scanner; `sk-...` and `DB_PASSWORD=hunter2` are simply
not things it looks for. Nothing is proved by this row except that we needed to
write it ourselves.

**UPI IDs 0/15.** Same again: no recognizer exists. Its lenient 6/15 comes from
calling parts of the IDs URLs or names.

### Where Presidio is right and our test data is wrong

**Aadhaar 0/4, even with the Indian recognizer loaded.** Not a Presidio failure.
`InAadhaarRecognizer` validates the Verhoeff checksum that real Aadhaar numbers
carry, and **our Aadhaar test numbers are invented, so they all fail it** —
verified directly with our own `passes_verhoeff()`, which returns False for
every one.

This is the same reason our own Verhoeff validator is written but not switched
on: turning it on would fail our own positive cases. Presidio didn't miss these
numbers; it correctly refused to believe them.

It did flag the Aadhaar case in the holdout, which was written independently.

### Where Presidio is stricter than us, deliberately

**Phones: 11/11 against 1/11.** Three separate causes, all verified:

- `+91 98765 43210` is labelled **LOCATION**, not a phone number.
- `+1 (555) 123-4567` is flagged as **nothing at all** — and Presidio is right.
  555 numbers are reserved for fiction, and Presidio validates against the real
  numbering plan, so it refuses a number that cannot exist. We match on shape,
  so we catch it.
- A bare `9876543210` is found — and simultaneously flagged as a **UK NHS
  number at confidence 1.0**.

## The cost of a general-purpose library

Out of the box, Presidio flags things in fourteen of our forty-nine must-pass
cases. Most don't map to our categories, so they don't count against it in the
table above — but a redactor wired to its output would act on them:

| what it flags | confidence | in text that must pass through           |
| ------------- | ---------- | --------------------------------------- |
| UK_NHS        | **1.0**    | `invoice 9876543210 is still unpaid`    |
| UK_NHS        | **1.0**    | `invoice no 9876543210 is overdue`      |
| UK_NHS        | **1.0**    | `the mobility grant ref 9876543210`     |
| UK_NHS        | **1.0**    | `after the data breach 9876543210 ...`  |
| IP_ADDRESS    | 0.6        | `upgrade the parser to version 1.2.3.4` |
| DATE_TIME     | 0.85       | `transaction ref 2024 1215 0930 4471`   |
| DATE_TIME     | 0.85       | `the unix timestamp was 1736899200`     |
| LOCATION      | 0.85       | `pin code 400001, Mumbai`               |

A UK NHS number is ten digits with a checksum. Indian mobile numbers and Indian
invoice references satisfy it by accident — at **confidence 1.0**, higher than
most of its correct answers. Anyone deploying Presidio in India would be
redacting invoice numbers on day one unless they knew to switch that recognizer
off.

With the Indian recognizers added it also flags `booking reference XYZAB1234C`,
`our company code is ABCPE1234F` and `check the control panel ABCPE1234F` as
PANs — the same false positives our own PAN rule was built to avoid, using the
holder-type character and a nearby word.

This is the honest lesson of the exercise: a library that detects more is not
the same as a library that is right more often. Every extra category is another
way to redact somebody's invoice number.

## What Presidio has that we don't

| entity   | cases | our position                                            |
| -------- | ----- | ------------------------------------------------------- |
| PERSON   | 4     | names are on the "later, not rejected" list             |
| URL      | 8     | not a category; usually not sensitive                   |
| LOCATION | 3     | not a category                                          |

`PERSON` is the real one. Names are the hardest PII to detect with patterns —
there is no shape to match — and Presidio does it with a language model. If we
ever add names, this is how, and this measurement is the argument for it.

## Decision

**Presidio is not a dependency for V1.** On the categories that matter to this
project it either scores lower, needs configuration we'd have to get right
anyway, or has no recognizer at all — and it brings false positives that would
undo the work Phase 2 spent avoiding them. It also costs a 700 MB install and a
language model on every deployment.

**Revisit in V2, for names only**, as an optional recognizer behind a rules-file
switch, with the irrelevant recognizers explicitly disabled.

The honest caveat, stated so nobody has to find it: these are our categories,
our test data, and our definition of correct. Presidio is built for a different
job — PII in English documents, with US defaults. On that job it would win.
