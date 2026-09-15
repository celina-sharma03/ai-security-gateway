"""Entry point.

python -m gateway                      show the configuration it loaded
python -m gateway check "some text"    run the pipeline and show what it did

Options for `check`:

--mode shadow|enforce   use this mode for one run, instead of GATEWAY_MODE
--rules PATH            use a different rules file

Phase 4 turns the bare command into the server.
"""

import argparse
import sys
from pathlib import Path

from gateway import __version__
from gateway.console import use_utf8_output
from gateway.engine.pipeline import PipelineResult, build_pipeline
from gateway.engine.rules import Rules, RulesError, load_rules
from gateway.settings import Mode, settings


def main(argv: list[str] | None = None) -> int:
    use_utf8_output()
    args = _parse(argv)

    rules_file = args.rules or settings.rules_file
    try:
        rules = load_rules(rules_file)
    except RulesError as exc:
        print(
            "The rules file has a problem, and the gateway won't start until it's fixed.\n",
            file=sys.stderr,
        )
        print(exc, file=sys.stderr)
        return 2

    if args.command == "check":
        mode = Mode(args.mode) if args.mode else settings.mode
        _show_check(build_pipeline(mode, rules).run(args.text))
    else:
        _show_config(rules_file, rules)

    return 0


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m gateway")
    parser.set_defaults(command=None, rules=None, mode=None)
    commands = parser.add_subparsers(dest="command")

    check = commands.add_parser("check", help="run the pipeline on some text")
    check.add_argument("text", help="the text to check, in quotes")
    check.add_argument(
        "--mode", choices=[m.value for m in Mode], help="shadow or enforce, for this run only"
    )
    check.add_argument("--rules", type=Path, help="a rules file to use instead of the default")

    return parser.parse_args(argv)


def _show_config(rules_file: Path, rules: Rules) -> None:
    print(f"AI Security Gateway {__version__}")
    print(f"  mode:     {settings.mode.value}")
    print(f"  rules:    {rules_file}")
    print(f"  upstream: {settings.upstream_base_url}")
    print(f"  listen:   {settings.host}:{settings.port}")
    print()
    print("What happens when each category is found:")
    for category, action in rules.actions.items():
        print(f"  {category:<14} {action.value if action else 'off'}")


def _show_check(result: PipelineResult) -> None:
    shadow_held_back = result.mode is Mode.SHADOW and result.action is not result.decided

    print(f"mode      {result.mode.value}")
    print(f"found     {result.decided.value:<8} what the checks asked for")
    print(
        f"done      {result.action.value:<8} what actually happened"
        + ("  (shadow mode changes nothing)" if shadow_held_back else "")
    )
    print()

    if result.blocked:
        print("sent onward: nothing -- the request is blocked")
    else:
        print("sent onward:")
        print(f"  {result.text}")

    print()
    print("checks:")
    for check in result.results:
        print(f"  {check.check:<10} {check.action.value:<8} {check.reason or 'nothing found'}")

    if result.errors:
        print()
        print("ERRORS -- these checks crashed and were skipped, so the text went on unchecked:")
        for error in result.errors:
            print(f"  {error}")


if __name__ == "__main__":
    sys.exit(main())
