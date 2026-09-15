"""The grader itself: a case is only correct when nothing extra was redacted."""

from eval.dataset import Case
from eval.score import run


def test_catching_the_secret_and_something_extra_is_a_failure():
    """The UPI ID is right to catch. The phone number beside it is not what this
    case expects, so the case must fail instead of passing on the UPI alone."""
    case = Case(text="pay ravi@ybl, or call 9876543210", expect=frozenset({"upi_id"}))

    report = run([case])

    assert report.over_redacted == 1
    assert report.caught == 0
    assert not report.clean


def test_catching_exactly_what_was_expected_passes():
    case = Case(text="pay me at ravi@ybl", expect=frozenset({"upi_id"}))

    report = run([case])

    assert report.caught == 1
    assert report.clean
