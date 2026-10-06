"""What a blocked request is told.

This is product copy, kept away from both the engine and the adapter. The
engine decides *that* something was found; the adapter knows what shape to
answer in; this file decides what the sentence says.

Three things the wording has to do:

  Say what kind of thing was found, in the words a person uses -- "a credit
  card number", not "credit_card".

  Never repeat the value. The whole point was to keep it out of the provider's
  logs; putting it in a refusal the caller might log would be absurd.

  Say that nothing was sent. Someone whose request was blocked wants to know
  whether their data left the building, and the answer is no.
"""

CATEGORY_NAMES = {
    "credit_card": "a credit card number",
    "api_key": "an API key",
    "password": "a password",
    "email": "an email address",
    "phone": "a phone number",
    "aadhaar": "an Aadhaar number",
    "pan": "a PAN",
    "upi_id": "a UPI ID",
    "ip_address": "an IP address",
}


def describe(categories: list[str]) -> str:
    """ "a credit card number and an email address", or the raw name if it is
    a custom category an operator added in rules.yaml."""
    named = [CATEGORY_NAMES.get(category, category) for category in categories]

    if not named:
        return "sensitive data"
    if len(named) == 1:
        return named[0]

    return f"{', '.join(named[:-1])} and {named[-1]}"


def refusal_message(categories: list[str]) -> str:
    return (
        "This request was blocked by your organisation's AI security gateway: "
        f"it contained {describe(categories)}. "
        "Nothing was sent to the provider."
    )
