"""What the HTTP layer accepts and returns.

These models are the API's contract, kept deliberately apart from the engine's
own shapes, for two reasons.

The engine's Finding carries `original` -- the secret itself. That must never
reach a response. A separate model makes leaking it something you'd have to do
on purpose, rather than something you forgot not to do.

And the engine has to stay free to change. If routes returned CheckResult
directly, renaming a field in the engine would break every client of the API.
"""

from pydantic import BaseModel, Field


class CheckRequest(BaseModel):
    text: str = Field(min_length=1, max_length=100_000)
    """The length limit is a guard, not a policy. Without one, a single request
    can hand the regex engine a gigabyte of text to chew through."""


class FindingOut(BaseModel):
    """One thing that was found, and where -- but never what it was.

    `original` is deliberately absent. See the module docstring.
    """

    category: str
    placeholder: str
    start: int
    end: int


class CheckResponse(BaseModel):
    mode: str
    """The mode the gateway is actually running in."""

    decided: str
    """What the checks asked for: allow, log, redact or block."""

    action: str
    """What would really happen to traffic right now. The same as `decided`,
    except in shadow mode, where redact and block both become log."""

    categories: list[str]

    redacted: str
    """The text as it would be sent onward in enforce mode."""

    findings: list[FindingOut]
