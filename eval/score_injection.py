"""Scores the injection check against the attack set, the negative set, and
whatever public data has been downloaded.

    python -m eval --injection --score

One number would be useless here, so there isn't one. The report gives:

* the catch rate split into **ordinary** and **tricky** cases, because
  patterns are supposed to do well on the famous phrasings and badly on the
  rest, and an average hides exactly that;
* the false-alarm rate per family, so a threshold can be blamed for the
  legitimate requests it costs;
* the same two numbers over the public data, which is thousands of real
  attacks rather than the forty-one anybody can think of.

A high overall catch rate here is not good news on its own. If the tricky
column is also high, either the patterns are better than expected or -- far
more likely -- the attack set has been written to match them, and the thing
to do is rewrite the set rather than celebrate.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from eval.dataset import Attack, attack_summary, load_attacks, load_benign, load_public
from gateway.engine.checks import injection
from gateway.engine.result import Action
from gateway.engine.rules import Rules

#: Injection set to block, which is the default, so a flagged case is one the
#: check acted on.
RULES = Rules()


def flagged(text: str) -> bool:
    return injection.check(text, rules=RULES).action is not Action.ALLOW


@dataclass
class Tally:
    caught: int = 0
    total: int = 0

    def add(self, hit: bool) -> None:
        self.total += 1
        self.caught += int(hit)

    @property
    def rate(self) -> float:
        return self.caught / self.total if self.total else 0.0

    def __str__(self) -> str:
        if not self.total:
            return "       --"
        return f"{self.caught:>4}/{self.total:<4} {self.rate:>5.0%}"


def tally(cases: Sequence[str]) -> Tally:
    counts = Tally()
    for text in cases:
        counts.add(flagged(text))
    return counts


def _attack_rows(attacks: Sequence[Attack]) -> None:
    print(f"  {'FAMILY':<24}{'ORDINARY':>16}{'TRICKY':>16}")
    for row in attack_summary():
        mine = [a for a in attacks if a.family == row.family]
        easy = tally([a.text for a in mine if not a.tricky])
        hard = tally([a.text for a in mine if a.tricky])
        print(f"  {row.family:<24}{easy!s:>16}{hard!s:>16}")

    easy = tally([a.text for a in attacks if not a.tricky])
    hard = tally([a.text for a in attacks if a.tricky])
    print(f"  {'':<24}{'':>16}{'':>16}")
    print(f"  {'all families':<24}{easy!s:>16}{hard!s:>16}")


def _false_alarms(benign: Sequence[Attack]) -> None:
    print(f"\n  {'LOOKS LIKE':<24}{'FALSE ALARMS':>16}")
    for row in attack_summary():
        near = [b.text for b in benign if b.looks_like == row.family]
        if near:
            print(f"  {row.family:<24}{tally(near)!s:>16}")

    ordinary = [b.text for b in benign if b.looks_like is None]
    if ordinary:
        print(f"  {'(ordinary traffic)':<24}{tally(ordinary)!s:>16}")

    print(f"  {'':<24}{'':>16}")
    print(f"  {'everything that must pass':<24}{tally([b.text for b in benign])!s:>16}")


def report() -> None:
    attacks = load_attacks()
    benign = load_benign()

    print("Injection check -- patterns only, no model loaded")
    print(f"{len(injection.SIGNATURES)} signatures, written from public jailbreak collections")
    print("and deliberately not from the tricky cases below.\n")

    print("CAUGHT (higher is better)\n")
    _attack_rows(attacks)

    print("\nFALSE ALARMS (lower is better -- these must all pass)")
    _false_alarms(benign)

    public = load_public()
    if not public:
        print("\nNo public data. `python -m eval --download` to score against real attacks.")
    else:
        real_attacks = tally([c.text for c in public if c.attack])
        real_benign = tally([c.text for c in public if not c.attack])
        print(f"\nPUBLIC DATA -- {len(public)} rows nobody here wrote\n")
        print(f"  {'real attacks caught':<24}{real_attacks!s:>16}")
        print(f"  {'real benign flagged':<24}{real_benign!s:>16}")

    _verdict(attacks, benign, public)


def _verdict(attacks: Sequence[Attack], benign: Sequence[Attack], public: Sequence) -> None:
    """What the numbers mean, stated rather than left for the reader to infer
    from a table they may be reading quickly."""
    hard = tally([a.text for a in attacks if a.tricky])
    alarms = tally([b.text for b in benign])

    print("\n" + "-" * 62)

    if hard.rate > 0.5:
        print("The tricky cases are being caught by keyword patterns, which should")
        print("not be possible. Either the patterns are aimed at our own test set,")
        print("or the set is not as hard as it claims. Check before believing it.")
    else:
        print(f"Patterns catch {hard.rate:.0%} of the tricky cases. That is the gap the")
        print("embedding model has to close, and the reason it is worth 80MB.")

    if alarms.caught:
        print(f"\n{alarms.caught} legitimate request(s) flagged. Each one is a person whose")
        print("work this would have interrupted, so each one is named:\n")
        for case in benign:
            result = injection.check(case.text, rules=RULES)
            if result.action is Action.ALLOW:
                continue
            shown = " ".join(case.text.split())
            print(f"  {shown[:66]!r}")
            print(f"      fired: {result.reason.removeprefix('matched ') if result.reason else ''}")
    else:
        print("\nNo legitimate request was flagged.")

    if public:
        real = tally([c.text for c in public if c.attack])
        print(f"\nOn real attacks nobody here wrote: {real.rate:.0%}.")
        print("That number is the one to quote. The rest is our own homework.")
