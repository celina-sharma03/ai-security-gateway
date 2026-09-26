"""Measures our patterns against Microsoft Presidio, on the same cases.

DECISIONS.md promised this comparison twice, and Phase 2 closed without it. This
script is that comparison: the same cases through both detectors, one table.

Presidio is deliberately *not* a project dependency. It is a large one-off
install -- the analyzer plus spaCy's large English model, around 700 MB -- so it
stays out of requirements.txt, and this script says how to get it if it's
missing:

    .venv/Scripts/python -m pip install presidio-analyzer
    .venv/Scripts/python -m spacy download en_core_web_lg
    .venv/Scripts/python -m eval.compare_presidio

Presidio is run twice, because one configuration would misrepresent it:

  presidio   exactly what `AnalyzerEngine()` gives you out of the box
  +india     the same, plus the PAN and Aadhaar recognizers that ship in the
             package but are not loaded by default, and a phone recognizer told
             that numbers are Indian

That second column matters. Without it this comparison would be a straw man:
Presidio *can* read a PAN, it just doesn't until you ask. Worth knowing either
way, because "out of the box" is what a developer in India actually gets.

A third column, `lenient`, gives the +india configuration credit for flagging
*anything* in the text, whatever it called it -- the most generous reading
available. Our own detector is scored strictly in every column, which is the
harder test.
"""

import sys
from collections import Counter

from eval.dataset import Case, load_holdout, load_negative, load_positive
from gateway.console import use_utf8_output
from gateway.engine.checks import redaction
from gateway.engine.result import Action

#: Presidio's entity names mapped to the categories this project uses. Only
#: these count. Everything Presidio finds outside this map is reported
#: separately -- those are the things we have no category for at all.
PRESIDIO_TO_OURS = {
    "CREDIT_CARD": "credit_card",
    "EMAIL_ADDRESS": "email",
    "PHONE_NUMBER": "phone",
    "IP_ADDRESS": "ip_address",
    "IN_PAN": "pan",
    "IN_AADHAAR": "aadhaar",
}

#: Presidio scores every match. Pattern matches score 0.5, checksum-validated
#: ones higher. Taking everything would count its weakest guesses; this is the
#: middle ground, stated out loud so the number can be argued with.
SCORE_THRESHOLD = 0.5


def build_analyzers() -> dict:
    """The two Presidio configurations, or a clear message about installing it."""
    try:
        from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
        from presidio_analyzer.predefined_recognizers import (
            InAadhaarRecognizer,
            InPanRecognizer,
            PhoneRecognizer,
        )
    except ImportError:
        print(__doc__.split("Presidio is run twice")[0].strip(), file=sys.stderr)
        raise SystemExit(2) from None

    default = RecognizerRegistry()
    default.load_predefined_recognizers()

    india = RecognizerRegistry()
    india.load_predefined_recognizers()
    india.add_recognizer(InPanRecognizer())
    india.add_recognizer(InAadhaarRecognizer())
    india.add_recognizer(PhoneRecognizer(supported_regions=["IN"]))

    return {
        "presidio": AnalyzerEngine(registry=default),
        "+india": AnalyzerEngine(registry=india),
    }


def ours_categories(case: Case) -> set[str]:
    return set(redaction.check(case.text).categories)


def entities(analyzer, case: Case) -> set[str]:
    found = analyzer.analyze(text=case.text, language="en", score_threshold=SCORE_THRESHOLD)
    return {r.entity_type for r in found}


def mapped(found: set[str]) -> set[str]:
    return {PRESIDIO_TO_OURS[e] for e in found if e in PRESIDIO_TO_OURS}


def group_of(case: Case) -> str:
    return sorted(case.expect)[0] if len(case.expect) == 1 else "several at once"


COLUMNS = ("ours", "presidio", "+india", "lenient")


def score_positives(analyzers: dict, cases: list[Case]) -> tuple[dict, Counter]:
    rows: dict[str, dict[str, int]] = {}
    unmapped: Counter = Counter()

    for case in cases:
        row = rows.setdefault(group_of(case), dict.fromkeys(("total", *COLUMNS), 0))
        row["total"] += 1

        default_found = entities(analyzers["presidio"], case)
        india_found = entities(analyzers["+india"], case)
        unmapped.update(e for e in india_found if e not in PRESIDIO_TO_OURS)

        if ours_categories(case) & case.expect:
            row["ours"] += 1
        if mapped(default_found) & case.expect:
            row["presidio"] += 1
        if mapped(india_found) & case.expect:
            row["+india"] += 1
        if india_found:
            row["lenient"] += 1

    return rows, unmapped


def score_negatives(analyzers: dict, cases: list[Case]) -> tuple[dict, list]:
    counts = dict.fromkeys(("total", *COLUMNS), 0)
    counts["total"] = len(cases)
    mistakes: list[tuple[Case, set[str]]] = []

    for case in cases:
        default_found = mapped(entities(analyzers["presidio"], case))
        india_found = entities(analyzers["+india"], case)

        if redaction.check(case.text).action is Action.ALLOW:
            counts["ours"] += 1
        if not default_found:
            counts["presidio"] += 1
        if not mapped(india_found):
            counts["+india"] += 1
        else:
            mistakes.append((case, mapped(india_found)))
        if not india_found:
            counts["lenient"] += 1

    return counts, mistakes


def show_table(title: str, rows: dict) -> None:
    print(f"\n{title} -- must be caught")
    header = "".join(f"{name:>11}" for name in COLUMNS)
    print(f"  {'':<16}{header}")

    totals = dict.fromkeys(("total", *COLUMNS), 0)
    for name, row in sorted(rows.items()):
        cells = "".join(f"{row[c]:>6} /{row['total']:>3}" for c in COLUMNS)
        print(f"  {name:<16}{cells}")
        for key in totals:
            totals[key] += row[key]

    cells = "".join(f"{totals[c]:>6} /{totals['total']:>3}" for c in COLUMNS)
    print(f"  {'TOTAL':<16}{cells}")


def show_negatives(title: str, counts: dict) -> None:
    print(f"\n{title} -- must pass through untouched")
    for name in COLUMNS:
        label = "+india, lenient" if name == "lenient" else name
        print(f"  {label:<18}{counts[name]:>3} / {counts['total']} clean")


def main() -> None:
    use_utf8_output()

    print("Loading Presidio twice (each one loads a language model)...")
    analyzers = build_analyzers()

    positives = load_positive()
    negatives = load_negative()
    holdout = load_holdout()

    print("\n" + "=" * 68)
    print("Our patterns vs Microsoft Presidio")
    print("=" * 68)
    print(f"  cases: {len(positives)} positive, {len(negatives)} negative, {len(holdout)} holdout")
    print(f"  presidio score threshold: {SCORE_THRESHOLD}")

    rows, unmapped = score_positives(analyzers, positives)
    show_table("Main set", rows)

    counts, mistakes = score_negatives(analyzers, negatives)
    show_negatives("Main set", counts)

    if mistakes:
        print("\n  What Presidio flags that must pass through:")
        for case, named in mistakes:
            text = " ".join(case.text.split())
            print(f"    {', '.join(sorted(named)):<24}{text[:50]!r}")

    hold_rows, _ = score_positives(analyzers, [c for c in holdout if c.should_flag])
    show_table("Holdout", hold_rows)

    hold_counts, hold_mistakes = score_negatives(
        analyzers, [c for c in holdout if not c.should_flag]
    )
    show_negatives("Holdout", hold_counts)
    for case, named in hold_mistakes:
        print(f"    {', '.join(sorted(named)):<24}{' '.join(case.text.split())[:50]!r}")

    if unmapped:
        print("\nWhat Presidio finds that we have no category for:")
        for entity, count in unmapped.most_common():
            print(f"  {entity:<24}{count:>4} cases")


if __name__ == "__main__":
    main()
