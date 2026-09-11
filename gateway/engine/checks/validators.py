"""Checksum validators.

Shape alone is not enough. A credit card number is not merely sixteen digits
-- real ones satisfy a checksum, and an order number almost never does. These
functions are what separate the two.
"""


def digits_only(value: str) -> str:
    return "".join(c for c in value if c.isdigit())


def passes_luhn(value: str) -> bool:
    """The checksum every real credit card number satisfies.

    Double every second digit from the right, subtract 9 from any result
    over 9, and the total must divide by 10.

    A random 16-digit number passes roughly one time in ten, so this is not
    proof -- but combined with a valid card prefix it is close enough that
    invoice numbers stop being flagged.
    """
    digits = digits_only(value)
    if len(digits) < 12:
        return False

    total = 0
    for index, char in enumerate(reversed(digits)):
        digit = int(char)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit

    return total % 10 == 0


#: Card numbers begin with the issuer's range: 3 American Express and Diners,
#: 4 Visa, 5 Mastercard, 6 Discover and RuPay. Nothing real begins with 0, 1,
#: 2, 7, 8 or 9, which rules out most invoice and reference numbers outright.
CARD_PREFIXES = ("3", "4", "5", "6")


def looks_like_card(value: str) -> bool:
    digits = digits_only(value)
    return 13 <= len(digits) <= 19 and digits.startswith(CARD_PREFIXES) and passes_luhn(digits)


# --- Verhoeff, used by Aadhaar -------------------------------------------
# Implemented and tested, but not currently wired into the Aadhaar pattern.
# Our test data is invented, and invented numbers do not satisfy a real
# checksum -- so switching it on would fail our own positive cases rather
# than prove anything. It goes live when the evaluation set holds properly
# generated numbers. See FRICTION.md.

_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)

_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)


def passes_verhoeff(value: str) -> bool:
    """The checksum a real Aadhaar number satisfies."""
    digits = digits_only(value)
    if not digits:
        return False

    check = 0
    for index, char in enumerate(reversed(digits)):
        check = _D[check][_P[index % 8][int(char)]]

    return check == 0


# --- PAN -----------------------------------------------------------------

#: The fourth character of a PAN encodes what kind of holder it belongs to:
#: P individual, C company, H Hindu undivided family, F firm, A association,
#: T trust, B body of individuals, L local authority, J artificial juridical
#: person, G government, K Krish (trust under a will). Every other letter is
#: structurally impossible, which is what separates a real PAN from a product
#: code of the same shape.
PAN_HOLDER_TYPES = frozenset("PCHFATBLJGK")


def looks_like_pan(value: str) -> bool:
    cleaned = value.strip().upper()
    if len(cleaned) != 10:
        return False
    return cleaned[3] in PAN_HOLDER_TYPES
