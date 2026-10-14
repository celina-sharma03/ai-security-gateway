"""Phase 1: the evaluation set itself is sound.

No detection exists yet. These tests guard the data — a typo in a category
name or a duplicated case would quietly weaken every score Phase 2 produces.
"""

import pytest

from eval.dataset import (
    KNOWN_FAMILIES,
    KNOWN_TYPES,
    attack_summary,
    load_all,
    load_attacks,
    load_holdout,
    load_negative,
    load_positive,
)


def test_both_files_load():
    assert load_positive()
    assert load_negative()


def test_every_positive_declares_what_it_contains():
    for case in load_positive():
        assert case.should_flag, f"positive case declares nothing to catch: {case.text!r}"


def test_no_negative_declares_anything():
    for case in load_negative():
        assert not case.should_flag, f"negative case expects a catch: {case.text!r}"


def test_categories_are_spelled_correctly():
    """A typo like 'credit_cards' would silently never be tested."""
    for case in load_positive():
        unknown = case.expect - KNOWN_TYPES
        assert not unknown, f"unknown category {unknown} in {case.text!r}"


def test_every_known_category_has_at_least_one_case():
    """If a category is worth listing, it's worth testing -- in the tuned set
    or the holdout."""
    covered = {kind for case in load_positive() + load_holdout() for kind in case.expect}
    missing = KNOWN_TYPES - covered
    assert not missing, f"categories with no test case: {missing}"


def test_no_duplicate_text():
    """Duplicates inflate a score without adding coverage."""
    seen: dict[str, int] = {}
    for case in load_all():
        key = " ".join(case.text.split())
        seen[key] = seen.get(key, 0) + 1

    duplicates = {text: n for text, n in seen.items() if n > 1}
    assert not duplicates, f"duplicated cases: {list(duplicates)[:3]}"


def test_no_case_is_empty():
    for case in load_all():
        assert case.text.strip(), "a case has no text"


def test_has_enough_hard_cases():
    """The tricky cases are what actually test Phase 2. A handful isn't enough."""
    tricky = [c for c in load_all() if c.tricky]
    assert len(tricky) >= 15, f"only {len(tricky)} tricky cases — the bar is low enough to fool"


def test_negatives_outnumber_positives():
    """False positives are the failure mode that gets a gateway uninstalled,
    so the set that guards against them should be the bigger one."""
    assert len(load_negative()) >= len(load_positive()) * 0.8


@pytest.mark.parametrize("case", load_positive(), ids=lambda c: repr(c.text[:40]))
def test_positive_case_is_well_formed(case):
    assert case.expect <= KNOWN_TYPES


@pytest.mark.parametrize("case", load_negative(), ids=lambda c: repr(c.text[:40]))
def test_negative_case_is_well_formed(case):
    assert case.expect == frozenset()


# --- Holdout -------------------------------------------------------------
# Graded, never tuned against. These tests only guard that the file is sound.
# Whether the gateway gets the cases right is measured with
# `python -m eval --holdout`, not enforced here.


def test_holdout_loads():
    assert load_holdout()


def test_holdout_categories_are_spelled_correctly():
    for case in load_holdout():
        unknown = case.expect - KNOWN_TYPES
        assert not unknown, f"unknown category {unknown} in {case.text!r}"


def test_holdout_does_not_repeat_the_tuned_set():
    """A holdout case copied from the tuned set would measure nothing new."""
    tuned = {" ".join(c.text.split()) for c in load_all()}
    for case in load_holdout():
        text = " ".join(case.text.split())
        assert text not in tuned, f"holdout repeats a tuned case: {case.text!r}"


# --- Phase 7: the prompt-injection attack set ----------------------------
# Same job as the tests above, for a file where the stakes are higher: these
# cases are what the thresholds get tuned against, so a typo here does not
# fail loudly, it just quietly removes a family from the tuning.


def test_attack_set_loads():
    assert load_attacks()


def test_every_attack_declares_a_family():
    for attack in load_attacks():
        assert attack.should_flag, f"attack with no family: {attack.text!r}"


def test_families_are_spelled_correctly():
    for attack in load_attacks():
        assert attack.family in KNOWN_FAMILIES, f"unknown family {attack.family!r}"


def test_every_known_family_has_cases():
    """A family worth naming is a family worth testing. Four cases is the
    floor at which a per-family score means anything at all."""
    for family, total, _ in attack_summary():
        assert total >= 4, f"{family} has only {total} cases"


def test_every_family_has_a_tricky_case():
    """The point of the embedding model is the attacks a keyword list misses.
    A family made only of famous phrasings would be a family where patterns
    score 100% and nothing has been learned."""
    for family, _, hard in attack_summary():
        assert hard >= 1, f"{family} has no case a keyword list would miss"


def test_most_attacks_are_tricky():
    """If the easy cases are the majority, a good overall score proves only
    that we can match the strings we wrote down ourselves."""
    attacks = load_attacks()
    tricky = sum(1 for a in attacks if a.tricky)
    assert tricky >= len(attacks) * 0.5, f"only {tricky} of {len(attacks)} are tricky"


def test_no_duplicate_attacks():
    seen: dict[str, int] = {}
    for attack in load_attacks():
        key = " ".join(attack.text.split())
        seen[key] = seen.get(key, 0) + 1

    duplicates = {text: n for text, n in seen.items() if n > 1}
    assert not duplicates, f"duplicated attacks: {list(duplicates)[:3]}"


def test_no_attack_is_empty():
    for attack in load_attacks():
        assert attack.text.strip(), "an attack has no text"


def test_the_obfuscated_cases_survived_being_written_down():
    """The encoding family only tests anything if the invisible characters
    are really in the file. A zero-width space eaten by an editor, or a
    \\u200b left as six literal characters, would turn the hardest case in
    the set into an easy one without changing how it looks on screen."""
    encoded = [a.text for a in load_attacks() if a.family == "encoding"]

    hidden = [t for t in encoded if "​" in t]
    assert hidden, "no case contains a real zero-width space"
    assert "\\u200b" not in "".join(encoded), "an escape was stored literally"
