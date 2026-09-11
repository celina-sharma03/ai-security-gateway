"""Run the redaction check over the evaluation set and report honestly.

Two numbers matter, and they are not equally important:

  recall     of the things that should be caught, how many were
  precision  of the things that were caught, how many should have been

A miss is bad. A false positive is worse, because it breaks ordinary work
and the gateway gets switched off that afternoon. The report below lists
every failure rather than only the totals, because a percentage with no
examples behind it is not evidence.
"""

from dataclasses import dataclass

from eval.dataset import Case, load_negative, load_positive
from gateway.engine.checks import redaction


@dataclass
class Failure:
    case: Case
    got: frozenset[str]
    detail: str


@dataclass
class Report:
    caught: int = 0
    missed: int = 0
    passed_clean: int = 0
    false_positives: int = 0

    misses: list[Failure] = None  # type: ignore[assignment]
    false_alarms: list[Failure] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.misses = self.misses or []
        self.false_alarms = self.false_alarms or []

    @property
    def recall(self) -> float:
        total = self.caught + self.missed
        return self.caught / total if total else 0.0

    @property
    def specificity(self) -> float:
        """How much ordinary traffic passes untouched."""
        total = self.passed_clean + self.false_positives
        return self.passed_clean / total if total else 0.0

    @property
    def clean(self) -> bool:
        return self.missed == 0 and self.false_positives == 0


def run() -> Report:
    report = Report()

    for case in load_positive():
        result = redaction.check(case.text)
        got = result.categories

        if got & case.expect:
            report.caught += 1
        else:
            report.missed += 1
            report.misses.append(
                Failure(
                    case=case,
                    got=got,
                    detail=f"expected {sorted(case.expect)}, got {sorted(got) or 'nothing'}",
                )
            )

    for case in load_negative():
        result = redaction.check(case.text)
        got = result.categories

        if not got:
            report.passed_clean += 1
        else:
            report.false_positives += 1
            report.false_alarms.append(
                Failure(
                    case=case,
                    got=got,
                    detail=f"flagged as {sorted(got)} -- {result.text}",
                )
            )

    return report


def _show(title: str, failures: list[Failure], weight: str) -> None:
    if not failures:
        return

    print(f"\n{title} ({len(failures)}) -- {weight}\n")
    for failure in failures:
        collapsed = " ".join(failure.case.text.split())
        shown = collapsed if len(collapsed) <= 70 else collapsed[:67] + "..."
        marker = "[tricky] " if failure.case.tricky else ""
        print(f"  {marker}{shown!r}")
        print(f"      {failure.detail}")
        if failure.case.note:
            print(f"      note: {failure.case.note}")
        print()


def report() -> Report:
    result = run()

    total_positive = result.caught + result.missed
    total_negative = result.passed_clean + result.false_positives

    print("Redaction check\n")
    print(f"  caught         {result.caught:>3} / {total_positive}   ({result.recall:.0%})")
    print(
        f"  passed clean   {result.passed_clean:>3} / {total_negative}   ({result.specificity:.0%})"
    )

    _show("MISSED", result.misses, "a secret got through")
    _show("FALSE POSITIVES", result.false_alarms, "ordinary text was redacted")

    if result.clean:
        print("\n  Everything correct.")

    return result
