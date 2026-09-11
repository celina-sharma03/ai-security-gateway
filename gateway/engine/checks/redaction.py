"""Finds personal data and secrets, and replaces them with placeholders.

Deliberately not an AI model. These values have rigid shapes, and a pattern
plus a checksum beats a model on both accuracy and speed -- an API key has no
meaning for a model to understand, it is just characters.

Placeholders are numbered per category and reused for repeated values, so the
same address twice becomes [EMAIL_1] twice. That consistency is what makes it
possible to put the real values back in the response later.
"""

import re

from gateway.engine.checks.patterns import PATTERNS, Pattern
from gateway.engine.result import Action, CheckResult, Finding

CHECK_NAME = "redaction"


class _Match:
    __slots__ = ("category", "start", "end", "text")

    def __init__(self, category: str, start: int, end: int, text: str) -> None:
        self.category = category
        self.start = start
        self.end = end
        self.text = text

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

            if pattern.validator is not None:
                try:
                    if not pattern.validator(value):
                        continue
                except (ValueError, IndexError):
                    continue

            if not _context_ok(pattern, text, match.start(), match.end()):
                continue

            found.append(_Match(pattern.category, start, end, value))

    return found


def _drop_overlaps(matches: list[_Match]) -> list[_Match]:
    """Keep the longest match where two patterns cover the same characters.

    A card number and a bare phone number can both match the same digits.
    The longer match is the more specific one.
    """
    ordered = sorted(matches, key=lambda m: (m.start, -m.length))

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
        key = (match.category, match.text)

        if key in seen:
            placeholder = seen[key]
        else:
            counters[match.category] = counters.get(match.category, 0) + 1
            placeholder = f"[{match.category.upper()}_{counters[match.category]}]"
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
