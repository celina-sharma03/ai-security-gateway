"""Entry point.

python -m gateway                      show the configuration it loaded
python -m gateway check "some text"    run the pipeline and show what it did
python -m gateway serve                run the proxy

python -m gateway keys create --tenant "Billing" --label "CI"
python -m gateway keys list
python -m gateway keys revoke gw_live_K3n8Qx7f

Options for `check`:

--mode shadow|enforce   use this mode for one run, instead of GATEWAY_MODE
--rules PATH            use a different rules file

Options for `serve`:

--host / --port         override where it listens, instead of GATEWAY_HOST/PORT
--reload                restart on file changes. Development only.

`serve` deliberately has no --mode or --rules. Those are read from the
environment, and a flag would only half-work: with --reload, uvicorn starts a
child process which re-reads the environment, while without it the app is
imported into this process where the settings have already been built. A flag
whose behaviour depends on another flag is worse than no flag.

    $env:GATEWAY_MODE = "enforce"
    python -m gateway serve
"""

import argparse
import asyncio
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

    # Note what has already happened by this point: the rules file was loaded
    # and validated above, before any command runs. So `serve` with a broken
    # rules file prints the explanation and stops, rather than starting a
    # server that dies on import with a traceback.

    if args.command == "check":
        mode = Mode(args.mode) if args.mode else settings.mode
        _show_check(build_pipeline(mode, rules).run(args.text))
    elif args.command == "serve":
        _serve(args.host or settings.host, args.port or settings.port, reload=args.reload)
    elif args.command == "keys":
        return asyncio.run(_keys(args))
    else:
        _show_config(rules_file, rules)

    return 0


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m gateway")
    parser.set_defaults(command=None, rules=None, mode=None)
    commands = parser.add_subparsers(dest="command")

    parser.set_defaults(host=None, port=None, reload=False)

    check = commands.add_parser("check", help="run the pipeline on some text")
    check.add_argument("text", help="the text to check, in quotes")
    check.add_argument(
        "--mode", choices=[m.value for m in Mode], help="shadow or enforce, for this run only"
    )
    check.add_argument("--rules", type=Path, help="a rules file to use instead of the default")

    keys = commands.add_parser("keys", help="issue, list and revoke gateway keys")
    key_commands = keys.add_subparsers(dest="keys_command", required=True)

    create = key_commands.add_parser("create", help="issue a key, shown once")
    create.add_argument("--tenant", required=True, help="whose traffic this is; created if new")
    create.add_argument("--label", default="unnamed", help='what it is for, e.g. "billing CI"')

    key_commands.add_parser("list", help="every key, revoked ones included")

    revoke = key_commands.add_parser("revoke", help="stop a key working")
    revoke.add_argument("prefix", help="the visible part, as shown by `keys list`")

    serve = commands.add_parser("serve", help="run the proxy")
    serve.add_argument("--host", help=f"default {settings.host}, or GATEWAY_HOST")
    serve.add_argument("--port", type=int, help=f"default {settings.port}, or GATEWAY_PORT")
    serve.add_argument(
        "--reload", action="store_true", help="restart on file changes (development only)"
    )

    return parser.parse_args(argv)


def _serve(host: str, port: int, *, reload: bool) -> None:
    """Start the server, having said where it will be.

    uvicorn is imported here rather than at the top of the file so that
    `python -m gateway check` stays a fast command that doesn't drag a web
    server into memory to look at a line of text.

    The app is passed as an import string rather than the object, because
    --reload needs to re-import it in a fresh child process.
    """
    import uvicorn

    print(f"AI Security Gateway {__version__}")
    print(f"  mode:      {settings.mode.value}")
    print(f"  rules:     {settings.rules_file}")
    print(f"  upstream:  {settings.upstream_base_url}")
    print(f"  key:       {_provider_key_line()}")
    print()

    if settings.upstream_api_key is None and not settings.passthrough_provider_key:
        print("  No provider key. Every proxied request will fail until you set")
        print("  GATEWAY_UPSTREAM_API_KEY, or allow callers to send their own with")
        print("  GATEWAY_PASSTHROUGH_PROVIDER_KEY=true.")
        print()

    print(f"  listening on  http://{host}:{port}")
    print(f"  point a client's base_url at  http://{host}:{port}/v1")
    print(f"  or try it in a browser:       http://{host}:{port}/docs")
    print()

    uvicorn.run("gateway.proxy.app:app", host=host, port=port, reload=reload)


async def _keys(args: argparse.Namespace) -> int:
    """The key commands. Async because the database is.

    Imported here rather than at the top of the file so that `check` -- which
    needs no database at all -- does not pay for loading SQLAlchemy.
    """
    from sqlalchemy.exc import OperationalError

    from gateway.auth import keys as keyring
    from gateway.storage import database

    try:
        async with database.sessions()() as session:
            if args.keys_command == "create":
                key, row = await keyring.issue(session, args.tenant, args.label)
                _show_new_key(key, args.tenant, row.label)

            elif args.keys_command == "list":
                _show_keys(await keyring.all_keys(session))

            elif args.keys_command == "revoke":
                revoked = await keyring.revoke(session, args.prefix)
                if revoked is None:
                    print(f"No key in use starts with {args.prefix}.", file=sys.stderr)
                    return 1
                print(f"Revoked {revoked.prefix} ({revoked.label}).")
                print("Requests using it are now refused.")

    except OperationalError:
        # Almost always the same thing: the database has never been created.
        print("The database isn't set up yet. Create it with:\n", file=sys.stderr)
        print("    python -m alembic upgrade head", file=sys.stderr)
        return 2

    return 0


def _show_new_key(key: str, tenant: str, label: str) -> None:
    print(f"Key created for {tenant} ({label}).\n")
    print(f"    {key}\n")
    print("That is the only time it will be shown. The gateway stores a hash of")
    print("it, so it cannot print the key again -- and neither can anyone who")
    print("steals the database.")


def _show_keys(keys: list) -> None:
    if not keys:
        print("No keys yet. Make one with:\n")
        print('    python -m gateway keys create --tenant "Billing" --label "CI"')
        return

    print(f"{'TENANT':<16}{'LABEL':<18}{'KEY':<20}{'CREATED':<12}{'LAST USED':<12}STATUS")

    for key in keys:
        used = key.last_used_at.strftime("%Y-%m-%d") if key.last_used_at else "never"
        print(
            f"{key.tenant.name:<16}{key.label:<18}{key.prefix:<20}"
            f"{key.created_at.strftime('%Y-%m-%d'):<12}{used:<12}"
            f"{'active' if key.active else 'revoked'}"
        )


def _provider_key_line() -> str:
    """Whether a provider key is configured -- never the key itself."""
    if settings.passthrough_provider_key:
        return "passthrough (callers send their own)"
    if settings.upstream_api_key is None:
        return "NOT SET -- proxied requests will be refused"
    return "set"


def _show_config(rules_file: Path, rules: Rules) -> None:
    print(f"AI Security Gateway {__version__}")
    print(f"  mode:     {settings.mode.value}")
    print(f"  rules:    {rules_file}")
    print(f"  upstream: {settings.upstream_base_url}")
    print(f"  key:      {_provider_key_line()}")
    print(f"  database: {settings.database_url}")
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
