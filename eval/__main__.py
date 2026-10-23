"""Show what's in the evaluation set, and how the checks score against it.

python -m eval            summary of the set
python -m eval --tricky   only the cases designed to fool a naive pattern
python -m eval --all      every case
python -m eval --score    run the redaction check and report every failure
python -m eval --holdout  score the independently written holdout cases instead

python -m eval --injection          the prompt-injection attack set, by family
python -m eval --injection --all    every attack, with the note on each one
python -m eval --download           fetch the public datasets (needs a network)
python -m eval --injection --holdout            the independently written cases
python -m eval --injection --holdout --semantic the same, with the model loaded
python -m eval --injection --buried             how deep an attack can hide
"""

import sys

from eval.dataset import (
    attack_summary,
    load_all,
    load_attacks,
    load_benign,
    load_holdout,
    load_negative,
    load_positive,
    load_public,
    public_manifest,
    summary,
)
from gateway.console import use_utf8_output


def show_summary() -> None:
    counts = summary()

    print("Evaluation set")
    print(f"  must be caught:   {counts['positive']:>3}")
    print(f"  must pass:        {counts['negative']:>3}")
    print(f"  deliberately hard:{counts['tricky']:>3}")
    print()
    print("By category:")
    for key, value in counts.items():
        if key.startswith("type:"):
            print(f"  {key.removeprefix('type:'):<14}{value:>3}")


def show_cases(cases: list) -> None:
    for case in cases:
        print(f"  {case}")
        if case.note:
            print(f"        -- {case.note}")


def _show_attacks(cases: list, *, label: str) -> None:
    print(f"\n{label}\n")
    for case in cases:
        print(f"  {'hard' if case.tricky else '    '}  {case}")
        if case.note:
            print(f"          -- {case.note}")


def show_injection(*, every: bool) -> None:
    attacks = load_attacks()
    benign = load_benign()

    print("Prompt-injection set")
    print(f"  must be caught:   {len(attacks):>3}   ({sum(1 for a in attacks if a.tricky)} tricky)")
    print(f"  must pass:        {len(benign):>3}   ({sum(1 for b in benign if b.tricky)} tricky)")
    print("\n(tricky means it shares its vocabulary with the other side of the set)\n")

    print(f"  {'FAMILY':<22}{'ATTACKS':>8}{'TRICKY':>8}{'NEAR-MISSES':>13}")
    for row in attack_summary():
        print(f"  {row.family:<22}{row.attacks:>8}{row.tricky:>8}{row.near_misses:>13}")

    unattributed = sum(1 for b in benign if b.looks_like is None)
    print(f"  {'(ordinary traffic)':<22}{'':>8}{'':>8}{unattributed:>13}")

    public = load_public()
    manifest = public_manifest()
    print()
    if not public:
        print("No public datasets downloaded. Run `python -m eval --download` to add")
        print("thousands of real attacks -- a score over 90 hand-written cases and a")
        print("score over 5,000 real ones are not the same claim.")
    else:
        # Not `attacks` -- that name already holds the hand-written list in
        # this function, and rebinding it to a count made `--all` crash with
        # "'int' object is not iterable" while the plain summary stayed fine,
        # because the summary returns before it reaches the loop.
        public_attacks = sum(1 for case in public if case.attack)
        print(
            f"Public datasets: {len(public)} rows ({public_attacks} attacks, "
            f"{len(public) - public_attacks} benign)"
        )
        for entry in manifest or []:
            print(f"  {entry['name']:<34}{entry['cases']:>6}   {entry['licence']}")

    if not every:
        print("\nRun with --all to read every case.")
        return

    for row in attack_summary():
        mine = [a for a in attacks if a.family == row.family]
        _show_attacks(mine, label=f"ATTACK -- {row.family}")

    print("\n" + "=" * 70)
    for row in attack_summary():
        near = [b for b in benign if b.looks_like == row.family]
        if near:
            _show_attacks(near, label=f"MUST PASS -- looks like {row.family}")
    ordinary = [b for b in benign if b.looks_like is None]
    _show_attacks(ordinary, label="MUST PASS -- ordinary traffic")


def main() -> None:
    use_utf8_output()
    args = set(sys.argv[1:])

    # --injection is read before --all for the same reason --holdout is read
    # alongside --score: one flag says which set, the other says how much of
    # it to print. Checking --all first would print the PII set to someone
    # who asked for the attacks.
    if "--download" in args:
        # Imported here, not at the top, so that nothing which merely reads
        # the evaluation set depends on a network client being importable.
        from eval.public import download

        download(refresh="--refresh" in args)
        return

    if "--injection" in args:
        # --injection says which set; --score and --all say what to do with
        # it. Read together, for the third time now, because the first time
        # cost a holdout score that silently graded the tuned set.
        if "--buried" in args:
            from eval.score_injection import buried_report

            buried_report()
            return

        if "--holdout" in args:
            from eval.score_injection import holdout_report

            holdout_report(semantic="--semantic" in args)
            return

        if "--score" in args:
            from eval.score_injection import report as injection_report

            injection_report()
            return

        show_injection(every="--all" in args)
        return

    if "--score" in args or "--holdout" in args:
        from eval.score import report

        # The two flags answer different questions -- --score is what to do,
        # --holdout is which cases to do it on -- so they have to be read
        # together. Checking --score first and returning meant that asking for
        # the holdout alongside it scored the tuned set instead, which is the
        # one thing the holdout exists to avoid.
        if "--holdout" in args:
            report(load_holdout(), title="Holdout -- written independently, never tuned against")
        else:
            report()
        return

    if "--tricky" in args:
        hard = [c for c in load_all() if c.tricky]
        print(f"{len(hard)} cases designed to fool a naive pattern:\n")
        show_cases(hard)
        return

    if "--all" in args:
        print("MUST BE CAUGHT\n")
        show_cases(load_positive())
        print("\nMUST PASS THROUGH\n")
        show_cases(load_negative())
        return

    show_summary()


if __name__ == "__main__":
    main()
