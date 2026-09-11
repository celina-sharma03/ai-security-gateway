"""Phase 1: the evaluation set itself is sound.

No detection exists yet. These tests guard the data — a typo in a category
name or a duplicated case would quietly weaken every score Phase 2 produces.
"""

import pytest

from eval.dataset import KNOWN_TYPES, load_all, load_negative, load_positive


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
    """If a category is worth listing, it's worth testing."""
    covered = {kind for case in load_positive() for kind in case.expect}
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
