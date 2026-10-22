"""Loads the evaluation cases from YAML into something tests can use.

There is no detection code here. This module only knows what the right
answer is, so Phase 2 has a target to hit.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

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

#: Public datasets, fetched by `python -m eval --download` and gitignored:
#: thousands of rows of other people's work under other people's licences.
#: Absent on a fresh clone, which the loader below treats as normal rather
#: than as an error -- the eval has to run without a network.
DOWNLOADED_DIR = DATA_DIR / "downloaded"

#: Legitimate requests that resemble attacks. The more important of the two
#: files: a missed injection is bad, a blocked support agent uninstalls the
#: gateway. Each case names the family it is a near-miss for, so false alarms
#: can be reported per family instead of as one average.
INJECTION_NEGATIVE_FILE = DATA_DIR / "injection_negative.yaml"

#: Written independently, graded once, never tuned against. Carries an
#: expected action per case, so the report can say where the gateway's
#: policy falls short of what the author thought should happen -- which is
#: more useful than a catch rate on its own.
INJECTION_HOLDOUT_FILE = DATA_DIR / "injection_holdout.yaml"

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

    #: On a benign case, the family it is a near-miss for. Never shown to
    #: the detector -- it exists so a false alarm can be attributed to the
    #: threshold that caused it.
    looks_like: str | None = None

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
            looks_like=case.get("looks_like"),
        )
        for case in raw["cases"]
    ]


def load_attacks() -> list[Attack]:
    """Hand-written attacks, every one of which must be caught."""
    return _load_attacks(INJECTION_POSITIVE_FILE)


def load_benign() -> list[Attack]:
    """Legitimate requests that resemble attacks, every one of which must
    pass through."""
    return _load_attacks(INJECTION_NEGATIVE_FILE)


class FamilyCounts(NamedTuple):
    """One row of the per-family summary.

    Named rather than a bare tuple because adding the near-miss count to a
    three-element tuple broke every caller that unpacked it positionally, in
    four places, for no reason other than the shape. Attribute access does
    not care how many fields there are.
    """

    family: str
    attacks: int
    tricky: int
    near_misses: int


@dataclass(frozen=True)
class PublicCase:
    """One row from a public dataset.

    No family, because these came with a yes-or-no label and nobody sorted
    fifteen thousand prompts into six families. Carries the source it came
    from, so a result can always be traced back to whose data produced it.
    """

    text: str
    attack: bool
    source: str

    @property
    def should_flag(self) -> bool:
        return self.attack


def load_public() -> list[PublicCase]:
    """Public rows, or nothing at all if they were never downloaded.

    An empty list rather than an error: a fresh clone has no network and
    must still be able to run the tests and score the committed sets. The
    report says plainly when it is working without them, because a score
    over 90 hand-written cases and a score over 5,000 real ones are not the
    same claim.
    """
    if not DOWNLOADED_DIR.is_dir():
        return []

    cases: list[PublicCase] = []
    for path in sorted(DOWNLOADED_DIR.glob("*.jsonl")):
        # split("\n") rather than splitlines(), because splitlines() also
        # breaks on U+2028, which JSON treats as an ordinary character and
        # real jailbreak prompts contain. See eval/public.py:as_line.
        for line in path.read_text(encoding="utf-8").split("\n"):
            if not line.strip():
                continue
            raw = json.loads(line)
            cases.append(
                PublicCase(text=raw["text"], attack=bool(raw["attack"]), source=raw["source"])
            )
    return cases


def public_manifest() -> list[dict] | None:
    """What was downloaded, from where, under which licence. None when
    nothing has been."""
    path = DOWNLOADED_DIR / "manifest.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class HoldoutCase:
    """One independently written case, and what should happen to it."""

    id: str
    text: str
    expected_action: str
    """block, log or allow. What the author thought should happen -- not a
    description of what the gateway does. The gap between the two is the
    point of the file."""

    severity: str = "medium"
    family: str | None = None
    rationale: str | None = None

    category: str | None = None
    """On a case that must pass, what kind of legitimate request it is.
    Separate from `family`, which names an attack family -- a security
    question and a jailbreak are not two values of one field."""

    difficulty: str | None = None
    """hard or easy, on a case that must pass.

    Two different failures. Flagging a hard one is the real limit of the
    approach, because the words genuinely overlap with an attack. Flagging
    an easy one means something is badly broken, and averaging the two
    together hides which has happened."""

    @property
    def should_flag(self) -> bool:
        return self.expected_action in ("block", "log")


def load_injection_holdout() -> list[HoldoutCase]:
    raw = yaml.safe_load(INJECTION_HOLDOUT_FILE.read_text(encoding="utf-8"))
    return [
        HoldoutCase(
            id=case["id"],
            text=case["text"],
            expected_action=case["expected_action"],
            severity=case.get("severity", "medium"),
            family=case.get("family"),
            rationale=case.get("rationale"),
            category=case.get("category"),
            difficulty=case.get("difficulty"),
        )
        for case in raw["cases"]
    ]


def attack_summary() -> list[FamilyCounts]:
    """Per family: attacks, how many of those a keyword list cannot catch,
    and how many benign cases are near-misses for it.

    Three numbers rather than one, because an overall score on this set could
    be carried entirely by the famous phrasings, and because a family with no
    near-misses is a family where a high catch rate costs nothing -- which is
    not the situation the gateway is actually in.
    """
    attacks = load_attacks()
    benign = load_benign()

    rows = [
        FamilyCounts(
            family=family,
            attacks=sum(1 for a in attacks if a.family == family),
            tricky=sum(1 for a in attacks if a.family == family and a.tricky),
            near_misses=sum(1 for b in benign if b.looks_like == family),
        )
        for family in sorted(KNOWN_FAMILIES)
    ]
    return sorted(rows, key=lambda row: -row.attacks)


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
