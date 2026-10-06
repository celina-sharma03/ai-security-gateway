"""Phase 5: reading the events back.

The last test in this file is the phase's exit criterion, asked the way an
operator would ask it -- from the command line, against a real database, with
two tenants' traffic in it.
"""

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gateway.__main__ import main
from gateway.auth import keys as keyring
from gateway.providers.base import Usage
from gateway.storage import events as event_log


def seed(url: str) -> None:
    """Two tenants, different traffic, written the way the proxy writes it."""

    async def build() -> None:
        engine = create_async_engine(url)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            _, billing = await keyring.issue(session, "Billing", "CI")
            _, support = await keyring.issue(session, "Support", "laptop")

            await event_log.record(
                session,
                key=billing,
                model="openai/gpt-oss-20b",
                mode="enforce",
                decided="redact",
                action="redact",
                categories=["credit_card"],
                usage=Usage(40, 20, 60),
                upstream_status=200,
                latency_ms=410,
            )
            await event_log.record(
                session,
                key=billing,
                model="openai/gpt-oss-20b",
                mode="enforce",
                decided="block",
                action="block",
                categories=["credit_card", "email"],
                upstream_status=None,
                latency_ms=12,
            )
            await event_log.record(
                session,
                key=support,
                model="openai/gpt-oss-20b",
                mode="enforce",
                decided="allow",
                action="allow",
                categories=[],
                usage=Usage(12, 8, 20),
                upstream_status=200,
                latency_ms=380,
            )
        await engine.dispose()

    asyncio.run(build())


# --- the list ------------------------------------------------------------


def test_an_empty_database_says_so(file_database, capsys):
    """Not an empty table. On a quiet week that is the answer, not a bug."""
    assert main(["events"]) == 0
    assert "Nothing recorded yet" in capsys.readouterr().out


def test_recent_events_are_listed(file_database, capsys):
    seed(file_database)

    assert main(["events"]) == 0

    out = capsys.readouterr().out
    assert "Billing" in out
    assert "Support" in out
    assert "credit_card" in out
    assert "redact" in out


def test_one_tenants_traffic_can_be_read_alone(file_database, capsys):
    seed(file_database)

    assert main(["events", "--tenant", "Support"]) == 0

    out = capsys.readouterr().out
    assert "Support" in out
    assert "Billing" not in out


def test_the_list_never_shows_what_was_in_the_request(file_database, capsys):
    """There is nothing to show -- which is the point. The category is the
    whole record of what was found."""
    seed(file_database)

    main(["events"])

    out = capsys.readouterr().out
    assert "4111" not in out
    assert "[CREDIT_CARD_1]" not in out
    assert "credit_card" in out


# --- the summary, and the exit criterion ---------------------------------


def test_the_summary_tells_the_two_tenants_apart(file_database, capsys):
    """**The Phase 5 exit criterion**, asked from the command line:

    two different keys' traffic is distinguishable in the database,
    and tenant labels are on every row.
    """
    seed(file_database)

    assert main(["events", "--summary"]) == 0

    out = capsys.readouterr().out
    lines = {line.split()[0]: line.split() for line in out.splitlines() if line[:1].isalpha()}

    # Billing: 2 requests, 1 blocked, 1 redacted, 60 tokens
    assert lines["Billing"][1:5] == ["2", "1", "1", "60"]
    # Support: 1 request, none blocked, none redacted, 20 tokens
    assert lines["Support"][1:5] == ["1", "0", "0", "20"]


def test_the_summary_counts_what_was_caught(file_database, capsys):
    seed(file_database)

    main(["events", "--summary"])

    out = capsys.readouterr().out
    assert "credit_card" in out
    assert "email" in out


def test_the_summary_shows_requests_per_key(file_database, capsys):
    """So a key nobody uses can be found, and revoked."""
    seed(file_database)

    main(["events", "--summary"])

    assert "Requests per key" in capsys.readouterr().out


def test_a_quiet_week_is_reported_rather_than_left_blank(file_database, capsys):
    """Events, but nothing caught in any of them."""

    async def build() -> None:
        engine = create_async_engine(file_database)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            _, key = await keyring.issue(session, "Billing", "CI")
            await event_log.record(
                session,
                key=key,
                model="m",
                mode="enforce",
                decided="allow",
                action="allow",
                categories=[],
            )
        await engine.dispose()

    asyncio.run(build())

    main(["events", "--summary"])

    out = capsys.readouterr().out
    assert "What was caught:" in out
    assert "nothing" in out
