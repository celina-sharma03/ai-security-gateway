"""Finds personal data and secrets, and replaces them with placeholders.

Deliberately not an AI model. These values have rigid shapes, and a pattern
plus a checksum beats a model on both accuracy and speed -- an API key has no
meaning for a model to understand, it is just characters.

Placeholders are numbered per kind of value and reused for repeated values, so
the same address twice becomes [EMAIL_1] twice. That consistency is what makes
it possible to put the real values back in the response later.

Most values are replaced whole. A UPI ID is the exception: only the name is
hidden, because the handle isn't personal and is usually what the answer needs.

What happens to each category -- redact, block, log, or not looked for at
all -- comes from the rules. See gateway/engine/rules.py.
"""

import re
from collections.abc import Iterable

from gateway.engine.checks.patterns import Pattern
from gateway.engine.result import Action, CheckResult, Finding
from gateway.engine.rules import Rules

CHECK_NAME = "redaction"

#: Every built-in category redacted -- the same as an empty rules file.
DEFAULT_RULES = Rules()


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


def _collect(text: str, patterns: Iterable[Pattern]) -> list[_Match]:
    found: list[_Match] = []

    for pattern in patterns:
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


def check(text: str, rules: Rules | None = None) -> CheckResult:
    """Scan text and return what was found, with a redacted version of it.

    `rules` decides which categories are looked for and what happens to each
    one. Without it, every built-in category is redacted.
    """
    if rules is None:
        rules = DEFAULT_RULES

    matches = _drop_overlaps(_collect(text, rules.patterns))

    action = Action.ALLOW
    counters: dict[str, int] = {}
    seen: dict[tuple[str, str], str] = {}
    findings: list[Finding] = []

    for match in matches:
        match_action = rules.action_for(match.category)
        if match_action is None:
            continue

        action = max(action, match_action)

        # Only redacted values get a placeholder. A logged or blocked value is
        # recorded as found, but left where it is.
        placeholder = ""
        if match_action is Action.REDACT:
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

    if not findings:
        return CheckResult.allow(CHECK_NAME)

    replaced = [f for f in findings if f.placeholder]
    categories = sorted({f.category for f in findings})

    return CheckResult(
        check=CHECK_NAME,
        action=action,
        findings=tuple(findings),
        text=_rewrite(text, replaced) if replaced else None,
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


#: Longest a held-back fragment can get before it is given up on.
#:
#: A placeholder is short -- `[CREDIT_CARD_1]` is fifteen characters. Anything
#: longer that started with `[` is not one, and holding it would stall the
#: stream waiting for a bracket that never closes. A model writing a citation
#: like `[1]` or a markdown link must not be able to do that.
LONGEST_PLACEHOLDER = 40


class Restorer:
    """Puts real values back into a reply that arrives in pieces.

    A placeholder can be split across two chunks:

        chunk 1:  "I'll email [EMA"
        chunk 2:  "IL_1] today"

    Substituting on each chunk alone finds nothing, and the person watching
    sees `[EMA` flicker onto their screen -- which is the exact thing
    reversible redaction exists to prevent.

    So a chunk ending in what might be a placeholder is held back, and goes
    out joined to the next one. The delay is a single chunk, and only on the
    chunks that end mid-placeholder.

    Deliberately stupid about what a placeholder looks like: an unclosed `[`
    is suspicious, anything else is not, and a `[` that stays unclosed for
    forty characters was never a placeholder at all.
    """

    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping
        self.pending = ""

    def feed(self, text: str) -> str:
        """Everything that is safe to send now."""
        if not self.mapping:
            return text

        whole = self.pending + text
        self.pending = ""

        cut = self._unclosed_bracket(whole)
        if cut is not None:
            self.pending = whole[cut:]
            whole = whole[:cut]

        return restore(whole, self.mapping)

    def flush(self) -> str:
        """Whatever is still held back, at the end of the stream.

        A placeholder that never completed is emitted as it stands. It is
        the model's own text, and swallowing it would be worse than showing
        a stray bracket.
        """
        left, self.pending = self.pending, ""
        return restore(left, self.mapping) if left else ""

    @staticmethod
    def _unclosed_bracket(text: str) -> int | None:
        """Where a possible placeholder starts, if the text ends inside one."""
        start = text.rfind("[")

        if start == -1 or "]" in text[start:]:
            return None

        # Too long to still be a placeholder, so it never was one.
        if len(text) - start > LONGEST_PLACEHOLDER:
            return None

        return start
