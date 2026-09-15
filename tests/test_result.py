"""The shared result shape."""

from gateway.engine.result import Action


def test_max_picks_the_strongest_action():
    """The pipeline relies on this. Action is also a str, so any comparison it
    doesn't define itself falls back to comparing the words alphabetically --
    and max() compares with >, where "redact" would beat "block"."""
    assert max([Action.ALLOW, Action.REDACT, Action.BLOCK]) is Action.BLOCK
    assert max([Action.REDACT, Action.BLOCK]) is Action.BLOCK
    assert max([Action.LOG, Action.REDACT]) is Action.REDACT
    assert max([Action.ALLOW, Action.LOG]) is Action.LOG


def test_every_comparison_follows_severity():
    assert Action.BLOCK > Action.REDACT
    assert Action.BLOCK >= Action.REDACT
    assert Action.REDACT < Action.BLOCK
    assert Action.REDACT <= Action.BLOCK


def test_actions_sort_by_severity():
    shuffled = [Action.REDACT, Action.ALLOW, Action.BLOCK, Action.LOG]
    assert sorted(shuffled) == [Action.ALLOW, Action.LOG, Action.REDACT, Action.BLOCK]
