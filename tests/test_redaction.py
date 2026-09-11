"""Phase 2: the redaction check."""

import pytest

from eval.dataset import load_negative, load_positive
from eval.score import run
from gateway.engine.checks import redaction
from gateway.engine.checks.validators import looks_like_card, looks_like_pan, passes_luhn
from gateway.engine.result import Action

# --- Against the evaluation set ------------------------------------------


def test_catches_everything_it_should():
    report = run()
    assert not report.misses, "\n".join(f.detail for f in report.misses)


def test_flags_nothing_it_should_not():
    """The failure that matters. A redacted invoice number gets the gateway
    switched off; a missed secret only gets it improved."""
    report = run()
    assert not report.false_alarms, "\n".join(f.detail for f in report.false_alarms)


@pytest.mark.parametrize("case", load_positive(), ids=lambda c: repr(c.text[:40]))
def test_positive_case(case):
    result = redaction.check(case.text)
    assert result.categories & case.expect, (
        f"expected {sorted(case.expect)}, got {sorted(result.categories)}"
    )


@pytest.mark.parametrize("case", load_negative(), ids=lambda c: repr(c.text[:40]))
def test_negative_case(case):
    result = redaction.check(case.text)
    assert result.action is Action.ALLOW, f"flagged as {sorted(result.categories)}"


# --- Behaviour of the result --------------------------------------------


def test_clean_text_returns_allow_and_changes_nothing():
    result = redaction.check("How do I center a div with flexbox?")

    assert result.action is Action.ALLOW
    assert result.text is None
    assert not result.findings


def test_redacted_text_no_longer_contains_the_secret():
    result = redaction.check("my card is 4111 1111 1111 1111")

    assert result.text is not None
    assert "4111" not in result.text
    assert "[CREDIT_CARD_1]" in result.text


def test_repeated_value_gets_the_same_placeholder():
    """Needed for restoration, and it stops one address becoming two tokens."""
    result = redaction.check("cc priya@example.com and priya@example.com")

    assert len(result.mapping) == 1
    assert result.text.count("[EMAIL_1]") == 2


def test_different_values_get_different_placeholders():
    result = redaction.check("cc priya@example.com and rahul@example.com")

    assert len(result.mapping) == 2
    assert "[EMAIL_1]" in result.text
    assert "[EMAIL_2]" in result.text


def test_round_trip_restores_the_original():
    """The V2 behaviour: the provider sees placeholders, the person sees
    their own data back."""
    original = "Write a follow-up to priya.sharma@example.com about invoice 4471"

    result = redaction.check(original)
    restored = redaction.restore(result.text, result.mapping)

    assert restored == original


def test_restore_survives_the_provider_rewording_around_it():
    result = redaction.check("email priya@example.com please")
    reply = f"Certainly -- I have drafted a note to {result.findings[0].placeholder} for you."

    restored = redaction.restore(reply, result.mapping)

    assert "priya@example.com" in restored
    assert "[EMAIL" not in restored


def test_reason_never_contains_the_secret():
    """The reason goes into logs. A log full of secrets is a worse leak than
    the one being prevented."""
    secret = "sk-not-a-real-key-0000000000000000000000000"
    result = redaction.check(f"my key is {secret}")

    assert result.reason
    assert secret not in result.reason
    assert "api_key" in result.reason


def test_several_categories_in_one_message():
    result = redaction.check(
        "Priya Sharma, priya@example.com, +91 98765 43210, card 4111 1111 1111 1111"
    )

    assert {"email", "phone", "credit_card"} <= result.categories


# --- Validators ----------------------------------------------------------


@pytest.mark.parametrize(
    "number",
    ["4111111111111111", "5500000000000004", "378282246310005", "6011111111111117"],
)
def test_real_test_cards_pass_luhn(number):
    assert passes_luhn(number)


@pytest.mark.parametrize("number", ["1234567890123456", "2024121509304471"])
def test_reference_numbers_fail_luhn(number):
    assert not passes_luhn(number)


def test_card_prefix_rules_out_non_card_numbers():
    """Nothing real starts with 0, 1, 2, 7, 8 or 9."""
    assert not looks_like_card("1234567890123456")
    assert looks_like_card("4111111111111111")


def test_pan_holder_type_character():
    assert looks_like_pan("ABCPE1234F")
    assert not looks_like_pan("ABCDE1234F"), "D is not a holder-type code"


# --- Context ------------------------------------------------------------
# The cases where shape alone genuinely cannot decide.


def test_same_digits_read_differently_by_context():
    """The clearest example in the whole set: identical numbers, opposite
    answers, decided entirely by the surrounding words."""
    assert redaction.check("my number is 9876543210").categories == {"phone"}
    assert redaction.check("invoice 9876543210 is still unpaid").categories == frozenset()


def test_version_string_is_not_an_ip_address():
    assert redaction.check("upgrade the parser to version 1.2.3.4").categories == frozenset()
    assert redaction.check("the user connected from 203.0.113.42").categories == {"ip_address"}
