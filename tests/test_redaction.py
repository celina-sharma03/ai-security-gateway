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
    [
        "4111111111111111",
        "5500000000000004",
        "378282246310005",
        "6011111111111117",
        "2223003122003222",
        "8150000000001231",
    ],
)
def test_real_test_cards_pass_luhn(number):
    assert passes_luhn(number)


@pytest.mark.parametrize("number", ["1234567890123456", "2024121509304471"])
def test_reference_numbers_fail_luhn(number):
    assert not passes_luhn(number)


def test_card_prefix_rules_out_non_card_numbers():
    assert not looks_like_card("1234567890123456")
    assert looks_like_card("4111111111111111")


def test_mastercard_2_series_and_rupay_are_cards():
    """Both were missed by the first version of the prefix rule, which assumed
    no real card starts with 2 or 8."""
    assert looks_like_card("2223003122003222")
    assert looks_like_card("8150000000001231")


@pytest.mark.parametrize("number", ["2220999999999991", "2721000000000004"])
def test_mastercard_2_series_range_is_exact(number):
    """Just outside 2221-2720. Both pass Luhn, so only the range keeps them out."""
    assert passes_luhn(number)
    assert not looks_like_card(number)


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


def test_context_word_must_be_a_whole_word():
    """A context word hiding inside another word used to count: 'pan' in
    'company', 'uid' in 'guide', 'reach' in 'breach'."""
    assert redaction.check("our company code is ABCPE1234F").action is Action.ALLOW
    assert redaction.check("PAN: ABCPE1234F").categories == {"pan"}


def test_common_forms_of_a_context_word_still_count():
    """Whole-word matching must not lose the everyday forms people write."""
    assert redaction.check("calling 9876543210 now").categories == {"phone"}
    assert redaction.check("both numbers are 9876543210 and 9123456789").categories == {"phone"}


# --- UPI ----------------------------------------------------------------


def test_upi_id_with_known_handle_needs_no_context():
    assert redaction.check("pay me at ravi@ybl").categories == {"upi_id"}


def test_upi_id_with_unknown_handle_needs_context():
    assert redaction.check("my upi is ravi@newbank").categories == {"upi_id"}
    assert redaction.check("ssh into admin@localhost").action is Action.ALLOW


def test_upi_match_wins_over_the_phone_number_inside_it():
    """Both patterns match the digits. The UPI match is the specific one, so the
    ID is treated as a UPI ID rather than a bare phone number."""
    result = redaction.check("my number and upi: 9812345678@paytm")
    assert result.categories == {"upi_id"}
    assert result.text == "my number and upi: [UPI_NAME_1]@paytm"


def test_email_is_not_mistaken_for_upi():
    assert redaction.check("email priya@gmail.com").categories == {"email"}


def test_upi_hides_the_name_and_keeps_the_handle():
    """The handle isn't personal, and it's what the answer depends on: the AI
    can only say "@axis isn't a real handle" if it can see @axis."""
    result = redaction.check("is this upi valid Celina@AXIS?")
    assert result.text == "is this upi valid [UPI_NAME_1]@AXIS?"


def test_upi_round_trip_restores_the_name():
    result = redaction.check("pay me at ravi@ybl")
    reply = f"Send it to {result.findings[0].placeholder}@ybl in PhonePe."

    assert redaction.restore(reply, result.mapping) == "Send it to ravi@ybl in PhonePe."


def test_upi_id_named_by_a_mobile_number_needs_no_context():
    result = redaction.check("send 200 to 9123456780@newbank")
    assert result.categories == {"upi_id"}
    assert result.text == "send 200 to [UPI_NAME_1]@newbank"


def test_upi_handle_that_names_a_bank_needs_no_context():
    assert redaction.check("Celina@AXIS?").categories == {"upi_id"}
