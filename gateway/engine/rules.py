"""Reads rules.yaml: what the operator wants done about each kind of value.

Detection itself stays in code, where it is tested. This file only decides what
happens when something is found -- redact it, block the request, log it, or
ignore it -- and lets an operator add patterns of their own.

A mistake in rules.yaml is an error, never a silent fallback. An operator who
writes `phnoe: off` believes phone numbers are no longer redacted, and carrying
on quietly would leave them wrong about their own gateway.
"""

import re
from enum import Enum
from functools import cached_property
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from gateway.engine.checks.patterns import PATTERNS, Pattern, _c
from gateway.engine.result import Action

#: Every category the built-in patterns can find.
BUILT_IN_CATEGORIES = frozenset(p.category for p in PATTERNS)


class RulesError(ValueError):
    """The rules file is missing, unreadable, or asks for something impossible."""


class Choice(str, Enum):
    """What an operator can ask for, per category."""

    REDACT = "redact"
    BLOCK = "block"
    LOG = "log"
    OFF = "off"

    @property
    def as_action(self) -> Action | None:
        """The action this choice produces, or None when the category is off."""
        return None if self is Choice.OFF else Action(self.value)


def _yaml_off(value: object) -> object:
    """YAML reads a bare `off` as the boolean false. In this file it means off.
    See FRICTION.md."""
    return Choice.OFF.value if value is False else value


class CustomPattern(BaseModel):
    """A pattern an operator adds, for something only their organisation has."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    pattern: str = Field(min_length=1)
    action: Choice = Choice.REDACT
    needs_context: tuple[str, ...] = ()

    @field_validator("name")
    @classmethod
    def _name_is_usable(cls, name: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise ValueError(
                "use lowercase letters, digits and underscores, starting with a letter"
            )
        if name in BUILT_IN_CATEGORIES:
            raise ValueError(f"'{name}' is a built-in category already -- choose another name")
        return name

    @field_validator("pattern")
    @classmethod
    def _pattern_compiles(cls, pattern: str) -> str:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"not a valid regular expression ({exc})") from exc
        return pattern

    @field_validator("action", mode="before")
    @classmethod
    def _read_off(cls, value: object) -> object:
        return _yaml_off(value)

    @field_validator("needs_context")
    @classmethod
    def _words_are_not_empty(cls, words: tuple[str, ...]) -> tuple[str, ...]:
        if any(not word.strip() for word in words):
            raise ValueError("context words can't be empty")
        return tuple(word.strip() for word in words)

    def to_pattern(self) -> Pattern:
        # Operators write words, not regex, so the words are escaped. They are
        # matched with the same whole-word helper as the built-in patterns, so
        # "emp" can't count inside "temp".
        context = None
        if self.needs_context:
            context = _c(*(re.escape(word) for word in self.needs_context))

        return Pattern(
            category=self.name,
            regex=re.compile(self.pattern),
            needs_context=context,
            label=f"custom:{self.name}",
        )


class Rules(BaseModel):
    """What to do about each category, plus the operator's own patterns."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    categories: dict[str, Choice] = {}
    custom: tuple[CustomPattern, ...] = ()

    @field_validator("categories", mode="before")
    @classmethod
    def _read_categories(cls, value: object) -> object:
        if value is None:
            return {}
        if isinstance(value, dict):
            return {key: _yaml_off(choice) for key, choice in value.items()}
        return value

    @field_validator("categories")
    @classmethod
    def _only_known_categories(cls, categories: dict[str, Choice]) -> dict[str, Choice]:
        unknown = sorted(set(categories) - BUILT_IN_CATEGORIES)
        if unknown:
            known = ", ".join(sorted(BUILT_IN_CATEGORIES))
            raise ValueError(
                f"unknown category {', '.join(unknown)} -- the categories are: {known}"
            )
        return categories

    @field_validator("custom", mode="before")
    @classmethod
    def _read_custom(cls, value: object) -> object:
        return () if value is None else value

    @model_validator(mode="after")
    def _custom_names_are_unique(self) -> "Rules":
        names = [custom.name for custom in self.custom]
        repeated = sorted({name for name in names if names.count(name) > 1})
        if repeated:
            raise ValueError(f"each custom pattern needs its own name: {', '.join(repeated)}")
        return self

    @cached_property
    def actions(self) -> dict[str, Action | None]:
        """Every category, and what happens when it's found. None means off.

        A built-in category missing from the file is redacted, so deleting a
        line can never quietly switch detection off.
        """
        actions = {
            category: self.categories.get(category, Choice.REDACT).as_action
            for category in sorted(BUILT_IN_CATEGORIES)
        }
        for custom in self.custom:
            actions[custom.name] = custom.action.as_action
        return actions

    @cached_property
    def patterns(self) -> tuple[Pattern, ...]:
        """The patterns to run: built-in ones for categories that aren't off, then
        the operator's own. Worked out once, not on every check."""
        built_in = [p for p in PATTERNS if self.actions[p.category] is not None]
        custom = [c.to_pattern() for c in self.custom if self.actions[c.name] is not None]
        return tuple(built_in + custom)

    def action_for(self, category: str) -> Action | None:
        """What happens when this category is found. None means it is off."""
        return self.actions[category]


def load_rules(path: Path) -> Rules:
    """Read and check a rules file.

    Raises RulesError with a message an operator can act on.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RulesError(f"can't read the rules file at {path} ({exc.strerror or exc})") from exc

    return parse_rules(text, source=str(path))


def parse_rules(text: str, *, source: str = "the rules") -> Rules:
    """Check rules written as YAML."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RulesError(f"{source} is not valid YAML:\n{exc}") from exc

    if data is None:
        return Rules()
    if not isinstance(data, dict):
        raise RulesError(f"{source} should contain settings such as 'categories:' and 'custom:'")

    try:
        return Rules.model_validate(data)
    except ValidationError as exc:
        raise RulesError(f"{source} has a problem:\n{_explain(exc)}") from exc


def _explain(exc: ValidationError) -> str:
    """Pydantic's errors, reworded for someone editing a YAML file."""
    lines = []
    for error in exc.errors():
        where = ".".join(str(part) for part in error["loc"]) or "the file"
        message = error["msg"].removeprefix("Value error, ")
        lines.append(f"  {where}: {message}")
    return "\n".join(lines)
