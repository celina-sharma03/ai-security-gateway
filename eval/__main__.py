"""Show what's in the evaluation set, and how the checks score against it.

python -m eval            summary of the set
python -m eval --tricky   only the cases designed to fool a naive pattern
python -m eval --all      every case
python -m eval --score    run the redaction check and report every failure
python -m eval --holdout  score the independently written holdout cases instead

python -m eval --injection          the prompt-injection attack set, by family
python -m eval --injection --all    every attack, with the note on each one
"""

import sys

from eval.dataset import (
    attack_summary,
    load_all,
    load_attacks,
    load_holdout,
    load_negative,
    load_positive,
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


def show_injection(*, every: bool) -> None:
    attacks = load_attacks()
    tricky = sum(1 for a in attacks if a.tricky)

    print(f"Prompt-injection attack set -- {len(attacks)} attacks, {tricky} of them tricky")
    print("(tricky means a keyword list cannot catch it)\n")

    print(f"  {'FAMILY':<22}{'CASES':>6}{'TRICKY':>8}")
    for family, total, hard in attack_summary():
        print(f"  {family:<22}{total:>6}{hard:>8}")

    if not every:
        print("\nRun with --all to read every attack.")
        return

    for family, _, _ in attack_summary():
        print(f"\n{family}\n")
        for attack in attacks:
            if attack.family == family:
                print(f"  {'hard' if attack.tricky else '    '}  {attack}")
                if attack.note:
                    print(f"          -- {attack.note}")


def main() -> None:
    use_utf8_output()
    args = set(sys.argv[1:])

    # --injection is read before --all for the same reason --holdout is read
    # alongside --score: one flag says which set, the other says how much of
    # it to print. Checking --all first would print the PII set to someone
    # who asked for the attacks.
    if "--injection" in args:
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
