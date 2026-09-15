"""rules.yaml: what happens to each category, and the operator's own patterns."""

import pytest
import yaml

from gateway.engine.checks import redaction
from gateway.engine.result import Action
from gateway.engine.rules import BUILT_IN_CATEGORIES, RulesError, load_rules, parse_rules
from gateway.settings import settings

# --- The shipped file ------------------------------------------------------


def test_shipped_rules_file_loads_and_redacts_everything():
    rules = load_rules(settings.rules_file)
    assert all(rules.action_for(c) is Action.REDACT for c in BUILT_IN_CATEGORIES)


def test_shipped_rules_file_lists_every_category():
    """An operator should be able to see every category without reading code."""
    raw = yaml.safe_load(settings.rules_file.read_text(encoding="utf-8"))
    assert set(raw["categories"]) == BUILT_IN_CATEGORIES


# --- Choosing what happens -------------------------------------------------


def test_empty_rules_mean_redact_everything():
    rules = parse_rules("")
    assert all(rules.action_for(c) is Action.REDACT for c in BUILT_IN_CATEGORIES)


def test_a_missing_category_is_still_redacted():
    """Deleting a line must never quietly switch detection off."""
    rules = parse_rules("categories:\n  email: log\n")
    assert rules.action_for("email") is Action.LOG
    assert rules.action_for("phone") is Action.REDACT


def test_off_written_plainly_in_yaml_switches_a_category_off():
    """YAML reads a bare `off` as the boolean false. It still has to mean off."""
    rules = parse_rules("categories:\n  phone: off\n")

    assert rules.action_for("phone") is None
    assert redaction.check("my number is 9876543210", rules=rules).action is Action.ALLOW


def test_block_stops_the_request():
    rules = parse_rules("categories:\n  api_key: block\n")
    result = redaction.check("key sk-not-a-real-key-0000000000000000000000000", rules=rules)

    assert result.action is Action.BLOCK


def test_log_records_without_changing_anything():
    rules = parse_rules("categories:\n  email: log\n")
    result = redaction.check("mail priya@example.com", rules=rules)

    assert result.action is Action.LOG
    assert result.categories == {"email"}
    assert result.text is None
    assert result.mapping == {}


def test_redact_and_log_in_one_message():
    rules = parse_rules("categories:\n  email: log\n")
    result = redaction.check("mail priya@example.com, card 4111 1111 1111 1111", rules=rules)

    assert result.action is Action.REDACT
    assert result.text == "mail priya@example.com, card [CREDIT_CARD_1]"


# --- Mistakes are errors, never silent -------------------------------------


def test_unknown_category_is_an_error():
    """`phnoe: off` would otherwise leave the operator believing phone numbers
    are no longer redacted."""
    with pytest.raises(RulesError, match="phnoe"):
        parse_rules("categories:\n  phnoe: off\n")


def test_unknown_action_is_an_error():
    with pytest.raises(RulesError, match="phone"):
        parse_rules("categories:\n  phone: remove\n")


def test_unexpected_setting_is_an_error():
    with pytest.raises(RulesError, match="categorys"):
        parse_rules("categorys:\n  phone: off\n")


def test_invalid_yaml_is_an_error_naming_the_file(tmp_path):
    path = tmp_path / "rules.yaml"
    path.write_text("categories:\n  phone: [unclosed\n", encoding="utf-8")

    with pytest.raises(RulesError, match="rules.yaml"):
        load_rules(path)


def test_missing_file_is_an_error(tmp_path):
    with pytest.raises(RulesError, match="can't read"):
        load_rules(tmp_path / "nope.yaml")


# --- The operator's own patterns ------------------------------------------


def test_custom_pattern_is_redacted():
    rules = parse_rules(r"""
custom:
  - name: employee_id
    pattern: '\bEMP-\d{5}\b'
""")
    result = redaction.check("ticket raised by EMP-48213", rules=rules)

    assert result.categories == {"employee_id"}
    assert result.text == "ticket raised by [EMPLOYEE_ID_1]"


def test_custom_pattern_can_block():
    rules = parse_rules(r"""
custom:
  - name: project_codename
    pattern: '\bBLUEFALCON\b'
    action: block
""")
    assert redaction.check("status of BLUEFALCON?", rules=rules).action is Action.BLOCK


def test_custom_pattern_switched_off_does_nothing():
    rules = parse_rules(r"""
custom:
  - name: employee_id
    pattern: '\bEMP-\d{5}\b'
    action: off
""")
    assert redaction.check("EMP-48213", rules=rules).action is Action.ALLOW


def test_custom_context_words_are_whole_words():
    """The same whole-word rule as the built-in patterns: 'emp' must not count
    inside 'temp'."""
    rules = parse_rules(r"""
custom:
  - name: employee_id
    pattern: '\b\d{5}\b'
    needs_context: [emp]
""")
    assert redaction.check("emp 48213", rules=rules).categories == {"employee_id"}
    assert redaction.check("temp 48213", rules=rules).action is Action.ALLOW


def test_custom_pattern_with_a_broken_regex_is_an_error():
    with pytest.raises(RulesError, match="regular expression"):
        parse_rules("custom:\n  - name: employee_id\n    pattern: 'EMP-(\\d{5}'\n")


def test_custom_pattern_cannot_reuse_a_built_in_name():
    with pytest.raises(RulesError, match="built-in"):
        parse_rules("custom:\n  - name: email\n    pattern: 'x'\n")
