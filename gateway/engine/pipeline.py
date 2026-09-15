"""Runs every check over a piece of text, in order, and decides what happens.

The pipeline doesn't know how any check works. It only needs each one to take
text and hand back a CheckResult -- which is why adding a check never means
changing this file.

Three rules live here and nowhere else:

  The strongest action wins. If one check says redact and another says block,
  the request is blocked.

  Shadow mode changes nothing. Every check still runs and every finding is
  recorded, but the original text goes onward untouched.

  A check that crashes fails open. Its error is recorded, and the text carries
  on as if that check had found nothing. A bug in one check must never take
  the application down with it.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial

from gateway.engine.checks import redaction
from gateway.engine.result import Action, CheckResult
from gateway.engine.rules import Rules
from gateway.settings import Mode

Check = Callable[[str], CheckResult]
"""Anything that takes text and returns a CheckResult."""


@dataclass(frozen=True)
class PipelineResult:
    mode: Mode

    decided: Action
    """What the checks asked for."""

    action: Action
    """What was actually done. The same as `decided`, except in shadow mode."""

    text: str
    """What goes onward to the provider. The original text in shadow mode."""

    results: tuple[CheckResult, ...]

    errors: tuple[str, ...] = ()
    """Checks that crashed and were skipped. Only the check's name and the type
    of error -- never the error message, which could quote the text itself."""

    @property
    def blocked(self) -> bool:
        return self.action is Action.BLOCK

    @property
    def categories(self) -> frozenset[str]:
        return frozenset().union(*(result.categories for result in self.results))

    @property
    def mapping(self) -> dict[str, str]:
        """Placeholder to original, for restoring values in the response.

        Empty in shadow mode, because nothing was replaced.
        """
        if self.mode is Mode.SHADOW:
            return {}

        merged: dict[str, str] = {}
        for result in self.results:
            merged.update(result.mapping)
        return merged


class Pipeline:
    def __init__(self, checks: Sequence[tuple[str, Check]], mode: Mode) -> None:
        self.checks = tuple(checks)
        self.mode = mode

    def run(self, text: str) -> PipelineResult:
        current = text
        decided = Action.ALLOW
        results: list[CheckResult] = []
        errors: list[str] = []

        for name, check in self.checks:
            try:
                result = check(current)
            except Exception as exc:  # failing open, deliberately -- see the module docstring
                errors.append(f"{name}: {type(exc).__name__}")
                continue

            results.append(result)
            decided = max(decided, result.action)

            # Each check works on the text the previous one produced.
            if result.text is not None:
                current = result.text

            # Once a request is blocked, nothing later can change that.
            if result.action is Action.BLOCK:
                break

        if self.mode is Mode.SHADOW:
            done = Action.LOG if decided in (Action.REDACT, Action.BLOCK) else decided
            onward = text
        else:
            done = decided
            onward = current

        return PipelineResult(
            mode=self.mode,
            decided=decided,
            action=done,
            text=onward,
            results=tuple(results),
            errors=tuple(errors),
        )


def build_pipeline(mode: Mode, rules: Rules) -> Pipeline:
    """The checks the gateway runs, in the order it runs them."""
    return Pipeline(
        checks=[("redaction", partial(redaction.check, rules=rules))],
        mode=mode,
    )
