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

#: Phase 7. Attacks the gateway must catch, grouped by family. Committed,
#: because these are what the thresholds get tuned against and a reader has
#: to be able to see them.
INJECTION_POSITIVE_FILE = DATA_DIR / "injection_positive.yaml"

#: Every family an attack is allowed to declare. A misspelled family would
#: silently create a seventh family of one case and leave a real one
#: untested, so the tests check this the same way they check KNOWN_TYPES.
KNOWN_FAMILIES = frozenset(
    {
        "instruction_override",
        "role_play",
        "prompt_extraction",
        "encoding",
        "context_stuffing",
        "indirect_injection",
    }
)

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


@dataclass(frozen=True)
class Attack:
    """One prompt-injection case: the text, and which family it belongs to.

    A case with a family must be caught. A case without one is benign and
    must pass through -- the same convention the holdout file already uses,
    where declaring `expect` is what makes a case a positive.
    """

    text: str
    family: str | None = None
    note: str | None = None
    tricky: bool = False

    @property
    def should_flag(self) -> bool:
        return self.family is not None

    def __str__(self) -> str:
        collapsed = " ".join(self.text.split())
        shown = collapsed if len(collapsed) <= 64 else collapsed[:61] + "..."
        return f"{'FLAG' if self.should_flag else 'PASS'}  {shown!r}"


def _load_attacks(path: Path) -> list[Attack]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [
        Attack(
            text=case["text"],
            family=case.get("family"),
            note=case.get("note"),
            tricky=bool(case.get("tricky", False)),
        )
        for case in raw["cases"]
    ]


def load_attacks() -> list[Attack]:
    """Hand-written attacks, every one of which must be caught."""
    return _load_attacks(INJECTION_POSITIVE_FILE)


def attack_summary() -> list[tuple[str, int, int]]:
    """Per family: how many cases, and how many of those a keyword list
    cannot catch. Reported separately because an overall score on this set
    could be carried entirely by the famous phrasings."""
    attacks = load_attacks()
    rows = [
        (
            family,
            sum(1 for a in attacks if a.family == family),
            sum(1 for a in attacks if a.family == family and a.tricky),
        )
        for family in sorted(KNOWN_FAMILIES)
    ]
    return sorted(rows, key=lambda row: -row[1])


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
