"""Show what's in the evaluation set, and how the checks score against it.

python -m eval            summary of the set
python -m eval --tricky   only the cases designed to fool a naive pattern
python -m eval --all      every case
python -m eval --score    run the redaction check and report every failure
python -m eval --holdout  run it on the independently written holdout cases
"""

import sys

from eval.dataset import load_all, load_holdout, load_negative, load_positive, summary
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


def main() -> None:
    use_utf8_output()
    args = set(sys.argv[1:])

    if "--score" in args:
        from eval.score import report

        report()
        return

    if "--holdout" in args:
        from eval.score import report

        report(load_holdout(), title="Holdout -- written independently, never tuned against")
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
