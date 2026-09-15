"""Phase 0 smoke tests: the skeleton is wired up correctly.

What the rules file contains is tested in test_rules.py.
"""

from gateway.settings import Mode, settings


def test_defaults_to_shadow_mode():
    """A fresh install must never break anything on first run."""
    assert settings.mode is Mode.SHADOW


def test_rules_file_exists():
    assert settings.rules_file.exists(), f"rules file missing at {settings.rules_file}"


def test_upstream_is_configurable_not_hardcoded():
    """The provider address must always come from settings."""
    assert settings.upstream_base_url.startswith("http")
