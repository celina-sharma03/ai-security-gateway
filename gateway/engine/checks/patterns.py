"""The patterns themselves.

Three things decide whether a match counts, and the second and third are what
separate this from a naive regex:

  regex      the shape
  validator  a checksum or structural rule the shape alone can't express
  context    words that must (or must not) appear nearby

Context is not decoration. Some values are genuinely indistinguishable by
shape: "9876543210" is a mobile number in `call me on 9876543210` and an
invoice reference in `invoice 9876543210 is unpaid`. Identical digits. Only
the surrounding words separate them, and no amount of pattern cleverness
will change that.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

from gateway.engine.checks.validators import looks_like_card, looks_like_pan


@dataclass(frozen=True)
class Pattern:
    category: str
    regex: re.Pattern[str]

    validator: Callable[[str], bool] | None = None
    """Run on the matched text. A match that fails is discarded."""

    needs_context: re.Pattern[str] | None = None
    """Must appear within `window` characters before the match."""

    blocked_by_context: re.Pattern[str] | None = None
    """If this appears nearby, the match is discarded."""

    window: int = 40

    group: int = 0
    """Which capture group holds the value. 0 means the whole match."""

    label: str = ""
    """For debugging, when one category has several patterns."""


def _c(*words: str) -> re.Pattern[str]:
    """A case-insensitive alternation of context words."""
    return re.compile(r"(?:" + "|".join(words) + r")", re.IGNORECASE)


PATTERNS: tuple[Pattern, ...] = (
    # --- Credit cards ----------------------------------------------------
    # 13 to 19 digits, optionally spaced or dashed. The validator does the
    # real work: a valid issuer prefix plus the Luhn checksum. Without it
    # every long reference number in the world gets redacted.
    Pattern(
        category="credit_card",
        regex=re.compile(r"\b\d(?:[ -]?\d){12,18}\b"),
        validator=looks_like_card,
        label="luhn",
    ),
    # --- API keys and secrets --------------------------------------------
    # Each provider stamps its keys with a prefix. That prefix plus a minimum
    # length is precise enough that commit hashes and UUIDs don't match.
    Pattern(
        category="api_key",
        regex=re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}"),
        label="openai/anthropic",
    ),
    Pattern(
        category="api_key",
        regex=re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        label="aws-access-key",
    ),
    Pattern(
        # AWS secret keys have no prefix -- they're 40 characters of base64
        # and look like any other random string. Only the surrounding name
        # makes them findable.
        category="api_key",
        regex=re.compile(
            r"(?i)aws_secret[a-z_]*\s*[=:]\s*([A-Za-z0-9/+=]{40})",
        ),
        group=1,
        label="aws-secret-key",
    ),
    Pattern(
        category="api_key",
        regex=re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
        label="github-token",
    ),
    Pattern(
        category="api_key",
        regex=re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
        label="slack-token",
    ),
    Pattern(
        category="api_key",
        regex=re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}"),
        label="jwt",
    ),
    Pattern(
        category="api_key",
        regex=re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
        label="pem-private-key",
    ),
    # --- Passwords -------------------------------------------------------
    # A password is just a word. Only the label in front of it gives it away.
    # No word boundary before the keyword, so DB_PASSWORD=... matches too.
    Pattern(
        category="password",
        regex=re.compile(
            r"(?i)(?:password|passwd|pwd)\s*(?:is|=|:)\s*([^\s,;]+)",
        ),
        group=1,
        label="labelled",
    ),
    # --- Email -----------------------------------------------------------
    # Requires a local part before the @, which is what keeps npm scoped
    # packages (@angular/core) and mentions (@channel) out.
    Pattern(
        category="email",
        regex=re.compile(
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
            r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*\.[A-Za-z]{2,}\b"
        ),
        label="standard",
    ),
    # --- Phone numbers ---------------------------------------------------
    # An explicit country code is self-identifying, so it needs no context.
    Pattern(
        category="phone",
        regex=re.compile(r"\+\d{1,3}[\s-]?\(?\d{2,5}\)?[\s-]?\d{3,5}[\s-]?\d{3,5}\b"),
        label="international",
    ),
    Pattern(
        # A bare ten-digit number is ambiguous by nature. Requiring a nearby
        # word is the only honest way to tell a mobile number from an invoice
        # reference -- they are the same digits.
        category="phone",
        regex=re.compile(r"\b0?[6-9]\d{9}\b"),
        needs_context=_c(
            "phone", "mobile", "number", "call", "whatsapp", "contact", "reach", "cell"
        ),
        label="bare-with-context",
    ),
    # --- Aadhaar ---------------------------------------------------------
    # Twelve digits, which is also an account number, an order id and a
    # timestamp. Context is doing all the work here.
    Pattern(
        category="aadhaar",
        regex=re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"),
        needs_context=_c("aadhaar", "aadhar", "uidai", "uid"),
        label="with-context",
    ),
    # --- PAN -------------------------------------------------------------
    # Five letters, four digits, one letter -- the same shape as plenty of
    # product and booking codes. Two rules are needed, and neither is enough
    # alone: the fourth character must be a real holder-type code, which rules
    # out most lookalikes outright, and a nearby word must say what it is,
    # because a booking reference can satisfy the structural rule by accident.
    Pattern(
        category="pan",
        regex=re.compile(r"\b[A-Za-z]{5}\d{4}[A-Za-z]\b"),
        validator=looks_like_pan,
        needs_context=_c("pan", "permanent account"),
        label="structural-with-context",
    ),
    # --- IP addresses ----------------------------------------------------
    # Version strings are the problem: 1.2.3.4 is a structurally valid IPv4.
    # Nothing in the shape distinguishes them, so nearby words rule them out.
    Pattern(
        category="ip_address",
        regex=re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
        validator=lambda v: all(0 <= int(p) <= 255 for p in v.split(".")),
        blocked_by_context=_c("version", "build", "release", r"\bv\d"),
        label="ipv4",
    ),
    Pattern(
        category="ip_address",
        regex=re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}\b"),
        label="ipv6",
    ),
)
