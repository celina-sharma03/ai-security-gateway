"""Finds personal data and secrets, and replaces them with placeholders.

Deliberately not an AI model. These values have rigid shapes, and a pattern
plus a checksum beats a model on both accuracy and speed -- an API key has no
meaning for a model to understand, it is just characters.

Placeholders are numbered per kind of value and reused for repeated values, so
the same address twice becomes [EMAIL_1] twice. That consistency is what makes
it possible to put the real values back in the response later.

Most values are replaced whole. A UPI ID is the exception: only the name is
hidden, because the handle isn't personal and is usually what the answer needs.
"""

import re

from gateway.engine.checks.patterns import PATTERNS, Pattern
from gateway.engine.result import Action, CheckResult, Finding

CHECK_NAME = "redaction"


class _Match:
    __slots__ = ("category", "start", "end", "text", "whole_length", "prefix")

    def __init__(
        self,
        category: str,
        start: int,
        end: int,
        text: str,
        whole_length: int,
        prefix: str,
    ) -> None:
        self.category = category
        self.start = start
        self.end = end
        self.text = text
        self.whole_length = whole_length
        self.prefix = prefix

    @property
    def length(self) -> int:
        return self.end - self.start


def _context_ok(pattern: Pattern, text: str, start: int, end: int) -> bool:
    """Check the words around a match, where the shape alone isn't decisive."""
    if pattern.needs_context is None and pattern.blocked_by_context is None:
        return True

    left = max(0, start - pattern.window)
    right = min(len(text), end + pattern.window)
    before = text[left:start]
    around = text[left:right]

    if pattern.blocked_by_context is not None and pattern.blocked_by_context.search(around):
        return False

    return pattern.needs_context is None or bool(pattern.needs_context.search(before))


def _collect(text: str) -> list[_Match]:
    found: list[_Match] = []

    for pattern in PATTERNS:
        for match in pattern.regex.finditer(text):
            start, end = match.span(pattern.group)
            value = match.group(pattern.group)

            if not value:
                continue

            # The validator sees the whole match. A UPI pattern replaces only the
            # name, but needs the handle to decide whether it's a UPI ID at all.
            if pattern.validator is not None:
                try:
                    if not pattern.validator(match.group(0)):
                        continue
                except (ValueError, IndexError):
                    continue

            if not _context_ok(pattern, text, match.start(), match.end()):
                continue

            found.append(
                _Match(
                    category=pattern.category,
                    start=start,
                    end=end,
                    text=value,
                    whole_length=match.end() - match.start(),
                    prefix=pattern.placeholder or pattern.category.upper(),
                )
            )

    return found


def _drop_overlaps(matches: list[_Match]) -> list[_Match]:
    """Keep the longest match where two patterns cover the same characters.

    A card number and a bare phone number can both match the same digits.
    The longer match is the more specific one. When the parts being replaced are
    the same length, the longer whole match wins: in "9812345678@paytm" the
    digits are the name of a UPI ID, not a phone number followed by stray text.
    """
    ordered = sorted(matches, key=lambda m: (m.start, -m.length, -m.whole_length))

    kept: list[_Match] = []
    for match in ordered:
        if any(match.start < k.end and k.start < match.end for k in kept):
            continue
        kept.append(match)

    return kept


def check(text: str) -> CheckResult:
    """Scan text and return what was found, with a redacted version of it."""
    matches = _drop_overlaps(_collect(text))

    if not matches:
        return CheckResult.allow(CHECK_NAME)

    counters: dict[str, int] = {}
    seen: dict[tuple[str, str], str] = {}
    findings: list[Finding] = []

    for match in matches:
        key = (match.prefix, match.text)

        if key in seen:
            placeholder = seen[key]
        else:
            counters[match.prefix] = counters.get(match.prefix, 0) + 1
            placeholder = f"[{match.prefix}_{counters[match.prefix]}]"
            seen[key] = placeholder

        findings.append(
            Finding(
                category=match.category,
                start=match.start,
                end=match.end,
                original=match.text,
                placeholder=placeholder,
            )
        )

    redacted = _rewrite(text, findings)
    categories = sorted({f.category for f in findings})

    return CheckResult(
        check=CHECK_NAME,
        action=Action.REDACT,
        findings=tuple(findings),
        text=redacted,
        # The reason names the categories but never the values, because it
        # gets written to logs and a log full of secrets is a worse leak
        # than the one being prevented.
        reason=f"found {len(findings)} value(s): {', '.join(categories)}",
    )


def _rewrite(text: str, findings: list[Finding]) -> str:
    """Swap each finding for its placeholder, working backwards so earlier
    offsets stay valid."""
    out = text
    for finding in sorted(findings, key=lambda f: f.start, reverse=True):
        out = out[: finding.start] + finding.placeholder + out[finding.end :]
    return out


def restore(text: str, mapping: dict[str, str]) -> str:
    """Put the real values back. Used on the response, in V2.

    Lives here so it stays next to the code that created the placeholders --
    the two must agree on the format or restoration silently does nothing.
    """
    if not mapping:
        return text

    pattern = re.compile("|".join(re.escape(k) for k in mapping))
    return pattern.sub(lambda m: mapping[m.group(0)], text)
