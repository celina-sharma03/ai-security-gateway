"""Phase 0 smoke tests: the skeleton is wired up correctly.

What the rules file contains is tested in test_rules.py.
"""

from gateway.settings import Mode, Settings, settings


def test_defaults_to_shadow_mode():
    """A fresh install must never break anything on first run."""
    assert settings.mode is Mode.SHADOW


def test_rules_file_exists():
    assert settings.rules_file.exists(), f"rules file missing at {settings.rules_file}"


def test_upstream_is_configurable_not_hardcoded():
    """The provider address must always come from settings."""
    assert settings.upstream_base_url.startswith("http")


# --- a variable left blank ----------------------------------------------
# Found while writing .env.example: someone copies it, leaves a line empty,
# and the gateway reads "" as a value rather than as "not filled in".


def test_a_blank_provider_key_is_no_key_at_all():
    """Without this the gateway believes it is configured, sends
    `Authorization: Bearer ` to the provider, and the provider answers 401 --
    sending whoever reads it to debug a key that was never the problem."""
    assert Settings(upstream_api_key="").upstream_api_key is None
    assert Settings(upstream_api_key="   ").upstream_api_key is None


def test_a_blank_setting_falls_back_to_its_default():
    """A blank line in a .env is a line nobody meant to write. It should not
    fail validation with a traceback."""
    assert Settings(mode="").mode is Mode.SHADOW
    assert Settings(port="").port == 8080
    assert Settings(upstream_base_url="").upstream_base_url == "https://api.openai.com/v1"


def test_a_real_value_still_arrives():
    """The guard drops blanks, not content."""
    assert Settings(mode="enforce").mode is Mode.ENFORCE
    assert Settings(upstream_api_key="sk-not-a-real-key-0000").upstream_api_key is not None


def test_a_wrong_value_is_still_an_error():
    """Blank means unset; nonsense still means nonsense."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(mode="enforc")
