"""The pipeline: order, the strongest action, shadow mode, and failing open."""

from gateway.engine.pipeline import Pipeline, build_pipeline
from gateway.engine.result import Action, CheckResult
from gateway.engine.rules import Rules, parse_rules
from gateway.settings import Mode

CARD = "my card is 4111 1111 1111 1111"


def _returns(action: Action):
    def check(text: str) -> CheckResult:
        return CheckResult(check="fake", action=action)

    return check


# --- Through the real redaction check --------------------------------------


def test_enforce_sends_the_redacted_text():
    result = build_pipeline(Mode.ENFORCE, Rules()).run(CARD)

    assert result.action is Action.REDACT
    assert result.text == "my card is [CREDIT_CARD_1]"


def test_shadow_sends_the_original_and_records_what_would_have_happened():
    result = build_pipeline(Mode.SHADOW, Rules()).run(CARD)

    assert result.text == CARD
    assert result.decided is Action.REDACT
    assert result.action is Action.LOG
    assert result.categories == {"credit_card"}


def test_shadow_turns_a_block_into_a_log():
    rules = parse_rules("categories:\n  credit_card: block\n")
    result = build_pipeline(Mode.SHADOW, rules).run(CARD)

    assert result.decided is Action.BLOCK
    assert result.action is Action.LOG
    assert not result.blocked
    assert result.text == CARD


def test_enforce_blocks():
    rules = parse_rules("categories:\n  credit_card: block\n")
    assert build_pipeline(Mode.ENFORCE, rules).run(CARD).blocked


def test_clean_text_is_allowed_in_both_modes():
    for mode in Mode:
        result = build_pipeline(mode, Rules()).run("How do I center a div?")

        assert result.action is Action.ALLOW
        assert result.text == "How do I center a div?"


def test_rules_from_config_reach_the_check():
    """The Phase 3 exit: redaction runs through the pipeline, reading its rules."""
    rules = parse_rules("categories:\n  phone: off\n")
    result = build_pipeline(Mode.ENFORCE, rules).run("my number is 9876543210")

    assert result.action is Action.ALLOW


def test_enforce_mapping_restores_values():
    result = build_pipeline(Mode.ENFORCE, Rules()).run(CARD)
    assert result.mapping == {"[CREDIT_CARD_1]": "4111 1111 1111 1111"}


def test_shadow_has_nothing_to_restore():
    """Nothing was replaced, so there is nothing to put back."""
    assert build_pipeline(Mode.SHADOW, Rules()).run(CARD).mapping == {}


# --- The pipeline's own rules, using stand-in checks -----------------------


def test_the_strongest_action_wins():
    log_then_redact = Pipeline(
        [("a", _returns(Action.LOG)), ("b", _returns(Action.REDACT))], Mode.ENFORCE
    )
    redact_then_block = Pipeline(
        [("a", _returns(Action.REDACT)), ("b", _returns(Action.BLOCK))], Mode.ENFORCE
    )

    assert log_then_redact.run("x").action is Action.REDACT
    assert redact_then_block.run("x").action is Action.BLOCK


def test_each_check_sees_the_text_the_previous_one_produced():
    seen = []

    def rewrite(text):
        return CheckResult(check="rewrite", action=Action.REDACT, text=text.upper())

    def record(text):
        seen.append(text)
        return CheckResult.allow("record")

    Pipeline([("rewrite", rewrite), ("record", record)], Mode.ENFORCE).run("hello")

    assert seen == ["HELLO"]


def test_a_block_stops_the_remaining_checks():
    called = []

    def later(text):
        called.append(text)
        return CheckResult.allow("later")

    pipeline = Pipeline([("first", _returns(Action.BLOCK)), ("later", later)], Mode.ENFORCE)

    assert pipeline.run("x").blocked
    assert called == []


def test_a_crashing_check_fails_open():
    def broken(text):
        raise RuntimeError("boom")

    result = Pipeline([("broken", broken)], Mode.ENFORCE).run("hello")

    assert result.action is Action.ALLOW
    assert result.text == "hello"
    assert result.errors == ("broken: RuntimeError",)


def test_a_crash_does_not_stop_the_other_checks():
    def broken(text):
        raise RuntimeError("boom")

    pipeline = Pipeline([("broken", broken), ("after", _returns(Action.REDACT))], Mode.ENFORCE)
    result = pipeline.run("x")

    assert result.action is Action.REDACT
    assert result.errors == ("broken: RuntimeError",)


def test_an_error_never_quotes_the_text():
    """Exception messages often include the value being processed. Recording
    one would write the secret into the very log the gateway protects."""
    secret = "sk-not-a-real-key-0000000000000000000000000"

    def leaky(text):
        raise ValueError(f"could not parse {text}")

    result = Pipeline([("leaky", leaky)], Mode.ENFORCE).run(secret)

    assert result.errors
    assert all(secret not in error for error in result.errors)
