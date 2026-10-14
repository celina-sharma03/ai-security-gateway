"""`python -m eval`: which set of cases each flag actually scores."""

import sys

import pytest

from eval.__main__ import main


@pytest.fixture
def run(monkeypatch, capsys):
    def _run(*flags: str) -> str:
        monkeypatch.setattr(sys, "argv", ["eval", *flags])
        main()
        return capsys.readouterr().out

    return _run


def test_score_reports_the_main_set(run):
    assert run("--score").startswith("Redaction check")


def test_holdout_reports_the_holdout(run):
    assert "Holdout" in run("--holdout")


def test_both_flags_together_still_report_the_holdout(run):
    """The flags answer different questions: --score is what to do, --holdout
    is which cases to do it on. Asking for both used to run the main set,
    because --score was checked first and returned -- and the tuned set is
    exactly what the holdout exists to avoid being graded on."""
    assert "Holdout" in run("--holdout", "--score")


def test_injection_reports_the_injection_set(run):
    assert "Prompt-injection set" in run("--injection")


def test_injection_with_all_still_reports_the_injection_set(run):
    """--all means "print every one", not "print the PII set". The same shape
    of bug as the one above, caught before it could happen this time."""
    out = run("--injection", "--all")
    assert "Prompt-injection set" in out
    assert "MUST BE CAUGHT" not in out, "printed the PII set instead"


def test_injection_shows_both_halves_of_the_set(run):
    """A summary that printed only the attacks would be the same mistake as
    the billing summary that showed six failed requests as healthy traffic:
    true as far as it goes, and it hides the number that matters."""
    out = run("--injection")
    assert "must be caught" in out
    assert "must pass" in out
    assert "NEAR-MISSES" in out
