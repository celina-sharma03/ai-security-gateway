"""Loads the evaluation cases from YAML into something tests can use.

There is no detection code here. This module only knows what the right
answer is, so Phase 2 has a target to hit.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

DATA_DIR = Path(__file__).resolve().parent / "data"

POSITIVE_FILE = DATA_DIR / "pii_positive.yaml"
NEGATIVE_FILE = DATA_DIR / "pii_negative.yaml"

#: Cases written independently of the two files above. Graded, never tuned
#: against -- that is what makes the score they produce believable.
HOLDOUT_FILE = DATA_DIR / "holdout.yaml"

#: Every category a positive case is allowed to declare. Anything outside
#: this set is a typo in the data file, and the tests will say so.
KNOWN_TYPES = frozenset(
    {
        "credit_card",
        "api_key",
        "password",
        "email",
        "phone",
        "aadhaar",
        "pan",
        "ip_address",
        "upi_id",
    }
)


@dataclass(frozen=True)
class Case:
    """One piece of text and what the gateway is supposed to make of it."""

    text: str
    expect: frozenset[str]
    note: str | None = None
    tricky: bool = False

    @property
    def should_flag(self) -> bool:
        """True when something in this text must be caught."""
        return bool(self.expect)

    def __str__(self) -> str:
        collapsed = " ".join(self.text.split())
        shown = collapsed if len(collapsed) <= 60 else collapsed[:57] + "..."
        return f"{'FLAG' if self.should_flag else 'PASS'}  {shown!r}"


def _parse(raw: dict, *, positive: bool) -> Case:
    expect: set[str] = set()

    if positive:
        if "expect" in raw:
            expect = {raw["expect"]}
        elif "expect_any_of" in raw:
            expect = set(raw["expect_any_of"])

    return Case(
        text=raw["text"],
        expect=frozenset(expect),
        note=raw.get("note"),
        tricky=bool(raw.get("tricky", False)),
    )


def _load(path: Path, *, positive: bool) -> list[Case]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [_parse(case, positive=positive) for case in raw["cases"]]


def load_positive() -> list[Case]:
    """Cases where something must be caught."""
    return _load(POSITIVE_FILE, positive=True)


def load_negative() -> list[Case]:
    """Cases that must pass through untouched."""
    return _load(NEGATIVE_FILE, positive=False)


def load_all() -> list[Case]:
    return load_positive() + load_negative()


def load_holdout() -> list[Case]:
    """Independently written cases, positives and negatives mixed in one file.

    A case that declares `expect` or `expect_any_of` must be caught; one that
    declares neither must pass through untouched.
    """
    raw = yaml.safe_load(HOLDOUT_FILE.read_text(encoding="utf-8"))
    return [
        _parse(case, positive="expect" in case or "expect_any_of" in case) for case in raw["cases"]
    ]


def summary() -> dict[str, int]:
    positive = load_positive()
    negative = load_negative()

    by_type: dict[str, int] = {}
    for case in positive:
        for kind in case.expect:
            by_type[kind] = by_type.get(kind, 0) + 1

    return {
        "positive": len(positive),
        "negative": len(negative),
        "tricky": sum(1 for c in load_all() if c.tricky),
        **{f"type:{k}": v for k, v in sorted(by_type.items())},
    }
