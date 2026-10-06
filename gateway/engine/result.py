"""The shape every check returns.

Nothing here knows about web requests, patterns, or models. It is only the
vocabulary the pipeline and the checks agree on.

Two things this deliberately supports that a simple true/false would not:

  - Redaction needs somewhere to put the cleaned text.
  - Restoring a redacted value in the response needs a mapping back to the
    original, so `[EMAIL_1]` can become the real address again on the way out.

The second isn't used until V2. The shape carries it from the start because
adding it later would mean changing every check that already exists.
"""

from dataclasses import dataclass, field
from enum import Enum


class Action(str, Enum):
    """What to do about what a check found.

    Ordered by severity. `max()` over several results gives the strongest
    action any check asked for.
    """

    ALLOW = "allow"
    LOG = "log"
    REDACT = "redact"
    BLOCK = "block"

    @property
    def severity(self) -> int:
        return _SEVERITY[self]

    # All four comparisons are needed, not only __lt__. Action is also a str, so
    # any comparison left undefined falls back to comparing the words
    # alphabetically -- and max() compares with >, where "redact" beats "block".
    def __lt__(self, other: "Action") -> bool:  # lt is less then here
        return self.severity < other.severity

    def __le__(self, other: "Action") -> bool:
        return self.severity <= other.severity

    def __gt__(self, other: "Action") -> bool:
        return self.severity > other.severity

    def __ge__(self, other: "Action") -> bool:
        return self.severity >= other.severity


_SEVERITY = {
    Action.ALLOW: 0,
    Action.LOG: 1,
    Action.REDACT: 2,
    Action.BLOCK: 3,
}


@dataclass(frozen=True)
class Finding:
    """One specific thing a check found, and where it was."""

    category: str
    """What kind of thing this is, e.g. "credit_card"."""

    start: int
    end: int
    """Character offsets into the text the check was given."""

    original: str
    """What was actually there. Never logged in cleartext."""

    placeholder: str = ""
    """What it was replaced with, e.g. "[CREDIT_CARD_1]". Empty if unchanged."""

    score: float | None = None
    """Set by score-based checks. Pattern checks leave this None, because a
    pattern either matched or it didn't."""

    def __str__(self) -> str:
        return f"{self.category}@{self.start}:{self.end}"


@dataclass(frozen=True)
class CheckResult:
    """What one check concluded about one piece of text."""

    check: str
    """Which check produced this, for logs and debugging."""

    action: Action = Action.ALLOW

    findings: tuple[Finding, ...] = field(default_factory=tuple)

    text: str | None = None
    """The rewritten text, when the check changed it. None means unchanged."""

    reason: str | None = None
    """Human-readable, safe to log. Must never contain the secret itself."""

    @property
    def changed_text(self) -> bool:
        return self.text is not None

    @property
    def mapping(self) -> dict[str, str]:
        """Placeholder to original, for restoring values in the response.

        This is what makes redaction reversible. Held only for the lifetime
        of a request — never stored.
        """
        return {f.placeholder: f.original for f in self.findings if f.placeholder}

    @property
    def categories(self) -> frozenset[str]:
        return frozenset(f.category for f in self.findings)

    @classmethod
    def allow(cls, check: str) -> "CheckResult":
        """Nothing found. The common case, by a wide margin."""
        return cls(check=check, action=Action.ALLOW)
