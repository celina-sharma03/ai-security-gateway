"""Finds attempts to override the instructions the operator set.

Patterns only, for now. The embedding model is the next step, and it is the
one that matters -- but patterns are kept rather than replaced, because they
cost microseconds, they never need a model to load, and the phrasings they
catch are the ones in every public jailbreak list.

**How these patterns were written, which is the part that keeps the
evaluation honest.** The attack set in `eval/data/injection_positive.yaml`
marks 28 of its 41 cases `tricky`, meaning no keyword list should catch them.
If the patterns here were written by reading those cases and targeting them
one by one, the word "tricky" would mean nothing and the measured gap between
patterns and embeddings would be a gap I had engineered away by cheating.

So they are written from what is publicly famous -- the phrasings in jailbreak
collections, security write-ups and the DAN lineage -- and not from our own
hard cases. The expected result is that patterns score well on the 13 ordinary
cases and close to nothing on the 28 tricky ones. That is not a weakness to be
tuned away. It is the measurement that says whether the embedding model earns
its 80MB.

**What is deliberately not a pattern.** `act as`, `pretend you are`, `stay in
character` and `from now on` all appear in attacks, and all four appear in the
negative set doing honest work: "act as a Python tutor", "pretend you are a
hiring manager and interview me", "stay in character as a French tutor",
"from now on, answer in British English". A pattern for any of them would
block language learners and interview practice to catch an attack the
embedding model should be catching on meaning instead.

Injection is not redacted. Removing the sentence that carries the attack
leaves a mutilated prompt and a person who cannot tell what happened, and the
attack is usually the whole point of the message rather than a part of it. So
the choices are block or log, and new installs get `block` -- which shadow
mode turns into a log until an operator has read a week of events.
"""

import re
from dataclasses import dataclass

from gateway.engine.result import CheckResult, Finding
from gateway.engine.rules import Rules

CHECK_NAME = "injection"

CATEGORY = "injection"


@dataclass(frozen=True)
class Signature:
    """One famous phrasing, and why it is in the list."""

    name: str
    regex: re.Pattern[str]
    note: str


def _p(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


SIGNATURES: tuple[Signature, ...] = (
    Signature(
        name="override_instructions",
        regex=_p(
            r"\b(?:ignore|disregard|forget|discard)\b"
            r"(?:\W+\w+){0,3}?\W+"
            r"\b(?:previous|prior|earlier|above|preceding|original|initial|all)\b"
            r"(?:\W+\w+){0,2}?\W+"
            r"\b(?:instruction|instructions|prompt|prompts|rule|rules|direction|directions|"
            r"directive|directives|guideline|guidelines|command|commands)\b"
        ),
        note="the canonical attack, in the shapes every jailbreak list uses",
    ),
    Signature(
        name="override_the_above",
        regex=_p(r"\b(?:ignore|disregard|forget)\b\W+(?:everything\W+)?(?:the\W+)?above\b"),
        note="the same thing with the noun left out",
    ),
    Signature(
        name="override_your_instructions",
        regex=_p(
            r"\b(?:ignore|disregard|forget|override|bypass)\b"
            r"(?:\W+\w+){0,2}?\W+your\W+"
            r"\b(?:instructions|programming|training|rules|guidelines|restrictions|"
            r"safeguards|filters?|system\W+prompt)\b"
        ),
        note="aimed at the model rather than at the conversation",
    ),
    Signature(
        name="override_the_gateway",
        regex=_p(
            r"\b(?:ignore|disregard|turn\W+off|switch\W+off|bypass|skip)\b"
            r"(?:\W+\w+){0,3}?\W+"
            r"\b(?:redaction|filtering|filters?|content\W+filter|safety\W+(?:checks?|filters?)|"
            r"security\W+(?:checks?|rules?))\b"
        ),
        note="aimed at this gateway. Someone who knows it is here will try it",
    ),
    Signature(
        name="do_anything_now",
        regex=_p(r"\bdo\W+anything\W+now\b|\bDAN\b(?=.{0,80}\b(?:mode|prompt|jailbr|rules?)\b)"),
        note="the DAN lineage, the most copied jailbreak there is",
    ),
    Signature(
        name="unrestricted_mode",
        regex=_p(
            r"\b(?:developer|dev|jailbreak|jailbroken|unrestricted|unfiltered|unlocked|god|"
            r"sudo|root|admin)\W+mode\b"
        ),
        note="an invented mode with no restrictions, named after the ones that circulate. "
        "'audit' and 'debug' were in this list and have been taken out: neither is in the "
        "public lineage, 'audit mode' was here only because our own test set invented it, "
        "and 'enable debug mode' is something people say about software",
    ),
    Signature(
        name="no_restrictions",
        regex=_p(
            r"\b(?:no|without|free\W+(?:of|from)|not\W+bound\W+by|free\W+from)\b"
            r"(?:\W+\w+){0,2}?\W+"
            r"\b(?:restrictions?|limitations?|guidelines?|content\W+polic\w+|rules?|filters?|"
            r"censorship|ethical\W+\w+)\b"
        ),
        note="declares the rules gone, which is what the persona jailbreaks all do",
    ),
    Signature(
        name="restrictions_lifted",
        regex=_p(
            r"\b(?:restrictions?|limitations?|filters?|rules?|safeguards?|guidelines?)\b"
            r"(?:\W+\w+){0,3}?\W+"
            r"\b(?:lifted|removed|disabled|suspended|revoked|rescinded|deactivated)\b"
            r"|\b(?:safety|content)\W+filtering\W+is\W+disabled\b"
        ),
        note="the passive version, which is how a forged system message phrases it",
    ),
    Signature(
        name="reveal_the_prompt",
        regex=_p(
            r"\b(?:reveal|show|print|output|repeat|display|disclose|divulge|leak)\b"
            r"(?:\W+(?:me|back|out))?\W+"
            r"(?:the\W+|your\W+|its\W+)"
            r"(?:(?:full|complete|entire|original|initial|exact|hidden|secret)\W+)?"
            r"(?:system\W+)?(?:prompt|instructions|message|configuration)\b"
        ),
        note="asks for the system prompt outright, which is step one of most attacks",
    ),
    Signature(
        name="override_claim",
        regex=_p(
            r"\bsystem\W+(?:override|overide)\b"
            r"|\b(?:system|admin|administrator|security\W+team|developer)\W+"
            r"(?:note|notice|message|directive|command)\s*[:\]]"
        ),
        note="user text wearing a system message costume",
    ),
    Signature(
        name="forged_turn",
        regex=_p(
            r"(?:^|\n)\s*(?:system|assistant)\s*:\s*\S"
            r"|---\s*(?:begin|end)\s+system\s+message\s*---"
            r'|"role"\s*:\s*"system"'
        ),
        note="writes the other side of the conversation, which the model reads as its own",
    ),
    Signature(
        name="chat_template_tokens",
        regex=_p(r"<\|im_(?:start|end)\|>|<\|(?:system|endoftext)\|>|\[/?INST\]|<<SYS>>"),
        note="the genuine delimiters, which ordinary prose has no reason to contain",
    ),
    Signature(
        name="decode_and_obey",
        regex=_p(
            r"\b(?:decode|decrypt|unscramble|reverse|translate|base64|rot13)\b"
            r"(?:\W+\w+){0,6}?\W+"
            r"\b(?:and|then)\b\W+"
            r"(?:\w+\W+){0,2}?"
            r"\b(?:follow|execute|obey|comply|run|do|perform|act)\b"
        ),
        note="the smuggling handle. 'decode and tell me' is a developer; "
        "'decode and follow' is an attack",
    ),
    Signature(
        name="stay_in_character_unconditionally",
        regex=_p(
            r"\b(?:never|do\W+not|don'?t)\W+break\W+(?:character|role)\b"
            r"|\bstay\W+in\W+character\W+(?:no\W+matter|whatever|regardless|at\W+all\W+costs)\b"
        ),
        note="not 'stay in character' on its own, which is how people practise a language. "
        "The refusal to ever stop is the part that is an attack",
    ),
)


def check(text: str, *, rules: Rules | None = None) -> CheckResult:
    """Look for known injection phrasings.

    Every signature that matches contributes a finding, so the event log can
    say which ones fired. The action comes from the rules, and is the same for
    all of them -- there is no useful sense in which one famous phrasing is
    more serious than another.
    """
    rules = rules or Rules()
    action = rules.injection.action.as_action

    if action is None:
        return CheckResult.allow(CHECK_NAME)

    findings = []
    names = []
    for signature in SIGNATURES:
        match = signature.regex.search(text)
        if match is None:
            continue

        names.append(signature.name)
        findings.append(
            Finding(
                category=CATEGORY,
                start=match.start(),
                end=match.end(),
                original=match.group(0),
                placeholder="",
            )
        )

    if not findings:
        return CheckResult.allow(CHECK_NAME)

    return CheckResult(
        check=CHECK_NAME,
        action=action,
        findings=tuple(findings),
        text=None,
        reason=f"matched {len(findings)} known injection phrasing(s): {', '.join(names)}",
    )
