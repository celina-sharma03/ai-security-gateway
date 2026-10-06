"""Phase 5: what gets written down.

These go through the real route and then read the real database file, because
the question is not "was record() called" but "what is actually in there".

The test that matters most is the one that reads the raw bytes of the file and
looks for the card number. Everything else is bookkeeping; that one is the
promise the schema was designed around.
"""

import asyncio
import pathlib

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from gateway.engine.pipeline import build_pipeline
from gateway.engine.rules import Rules, parse_rules
from gateway.proxy.app import app, get_pipeline, get_upstream
from gateway.proxy.upstream import Upstream
from gateway.settings import Mode
from gateway.storage.models import Event
from tests.stubs import TEST_PROVIDER_KEY, StubProvider

CARD = "my card is 4111 1111 1111 1111"
CARD_REQUEST = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": CARD}]}
CLEAN_REQUEST = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "what is 2+2?"}]}


@pytest.fixture
def stub() -> StubProvider:
    return StubProvider()


@pytest.fixture
def client(stub: StubProvider, gateway_key: str):
    app.dependency_overrides[get_upstream] = lambda: Upstream(
        transport=stub.transport, api_key=TEST_PROVIDER_KEY, passthrough=False
    )
    app.dependency_overrides[get_pipeline] = lambda: build_pipeline(Mode.ENFORCE, Rules())
    yield TestClient(app, headers={"Authorization": f"Bearer {gateway_key}"})
    app.dependency_overrides.clear()


def use_upstream(stub: StubProvider, **kwargs) -> None:
    app.dependency_overrides[get_upstream] = lambda: Upstream(transport=stub.transport, **kwargs)


def recorded(file_database: str) -> list[Event]:
    """Every event in the database the app just wrote to."""

    async def read() -> list[Event]:
        engine = create_async_engine(file_database)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                result = await session.execute(
                    select(Event).options(
                        selectinload(Event.categories),
                        selectinload(Event.tenant),
                        selectinload(Event.api_key),
                    )
                )
                return list(result.scalars())
        finally:
            await engine.dispose()

    return asyncio.run(read())


# --- the ordinary path ---------------------------------------------------


def test_a_forwarded_request_is_recorded(client, file_database):
    client.post("/v1/chat/completions", json=CLEAN_REQUEST)

    (event,) = recorded(file_database)

    assert event.tenant.name == "Billing"
    assert event.api_key.label == "tests"
    assert event.model == "gpt-4o-mini"
    assert event.mode == "shadow"  # settings default; the pipeline was overridden, not the mode
    assert event.action == "allow"
    assert event.upstream_status == 200
    assert event.blocked is False
    assert event.error is None
    assert event.latency_ms is not None


def test_what_was_found_is_recorded_by_category(client, file_database):
    client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": f"{CARD}, mail priya@example.com"}],
        },
    )

    (event,) = recorded(file_database)

    assert sorted(c.category for c in event.categories) == ["credit_card", "email"]
    assert event.decided == "redact"


def test_a_clean_request_records_no_categories(client, file_database):
    client.post("/v1/chat/completions", json=CLEAN_REQUEST)

    (event,) = recorded(file_database)

    assert event.categories == []
    assert event.decided == "allow"


def test_every_request_gets_its_own_row(client, file_database):
    for _ in range(3):
        client.post("/v1/chat/completions", json=CLEAN_REQUEST)

    assert len(recorded(file_database)) == 3


# --- the promise ---------------------------------------------------------


def test_the_database_file_never_contains_the_card_number(client, file_database):
    """Read as raw bytes, so no amount of clever querying can hide it.

    A gateway that wrote prompts into its own database would have moved the
    problem rather than solved it: one breach would leak everything anyone had
    ever typed.
    """
    client.post("/v1/chat/completions", json=CARD_REQUEST)

    path = file_database.removeprefix("sqlite+aiosqlite:///")
    contents = pathlib.Path(path).read_bytes()

    assert b"4111 1111 1111 1111" not in contents
    assert b"4111111111111111" not in contents
    assert b"[CREDIT_CARD_1]" not in contents
    assert b"credit_card" in contents  # the category, which is the whole point


# --- blocking and failing ------------------------------------------------


def test_a_blocked_request_is_recorded_as_blocked(client, file_database, stub):
    app.dependency_overrides[get_pipeline] = lambda: build_pipeline(
        Mode.ENFORCE, parse_rules("categories:\n  credit_card: block\n")
    )

    client.post("/v1/chat/completions", json=CARD_REQUEST)

    (event,) = recorded(file_database)

    assert event.action == "block"
    assert event.blocked is True
    assert event.upstream_status is None  # it never reached anyone
    assert not stub.called


def test_an_upstream_failure_is_recorded(client, file_database, stub):
    """The provider being down at 3am is exactly what this log is for."""
    use_upstream(
        StubProvider(error=httpx.ConnectTimeout("too slow")),
        api_key=TEST_PROVIDER_KEY,
        passthrough=False,
    )

    client.post("/v1/chat/completions", json=CLEAN_REQUEST)

    (event,) = recorded(file_database)

    assert event.error == "UpstreamTimeout"
    assert event.upstream_status is None


def test_the_provider_errors_status_is_kept(client, file_database):
    """A 429 is the provider's answer, and the log says which one it was."""
    use_upstream(
        StubProvider(status_code=429, body={"error": {"message": "slow down"}}),
        api_key=TEST_PROVIDER_KEY,
        passthrough=False,
    )

    client.post("/v1/chat/completions", json=CLEAN_REQUEST)

    (event,) = recorded(file_database)

    assert event.upstream_status == 429
    assert event.error is None  # not our failure -- their answer


# --- shadow mode ---------------------------------------------------------


def test_shadow_mode_records_both_what_it_wanted_and_what_it_did(client, file_database):
    """The two columns exist for this. Reading a month-old log without them
    would leave you unable to say whether anything was ever enforced."""
    app.dependency_overrides[get_pipeline] = lambda: build_pipeline(Mode.SHADOW, Rules())

    client.post("/v1/chat/completions", json=CARD_REQUEST)

    (event,) = recorded(file_database)

    assert event.decided == "redact"
    assert event.action == "log"


# --- when the log itself fails -------------------------------------------


def test_a_failure_to_record_does_not_fail_the_request(client, file_database, monkeypatch, caplog):
    """The work is already done by then: the checks ran, the provider
    answered, and the caller is about to get their reply. Turning that into a
    500 because a row could not be written would mean an outage every time the
    database hiccups.
    """

    async def broken(*args, **kwargs):
        raise RuntimeError("the disk is full")

    monkeypatch.setattr("gateway.storage.events.record", broken)

    response = client.post("/v1/chat/completions", json=CLEAN_REQUEST)

    assert response.status_code == 200
    assert recorded(file_database) == []
    assert "RuntimeError" in caplog.text
    assert "disk is full" not in caplog.text  # the type, never the message


# --- what it cost --------------------------------------------------------


def test_the_providers_token_counts_are_recorded(client, file_database):
    """The stub answers with usage 9 / 7 / 16, the way a provider does."""
    client.post("/v1/chat/completions", json=CLEAN_REQUEST)

    (event,) = recorded(file_database)

    assert event.prompt_tokens == 9
    assert event.completion_tokens == 7
    assert event.total_tokens == 16


def test_a_reply_without_usage_records_nothing_rather_than_zero(client, file_database):
    use_upstream(
        StubProvider(body={"choices": []}),
        api_key=TEST_PROVIDER_KEY,
        passthrough=False,
    )

    client.post("/v1/chat/completions", json=CLEAN_REQUEST)

    (event,) = recorded(file_database)

    assert event.total_tokens is None


def test_a_blocked_request_has_no_token_counts(client, file_database):
    """Null because it never reached them, not zero. Zero would be a claim
    about what the provider charged, and the provider was never asked."""
    app.dependency_overrides[get_pipeline] = lambda: build_pipeline(
        Mode.ENFORCE, parse_rules("categories:\n  credit_card: block\n")
    )

    client.post("/v1/chat/completions", json=CARD_REQUEST)

    (event,) = recorded(file_database)

    assert event.total_tokens is None
    assert event.blocked is True


def test_what_a_tenant_spent_can_be_added_up(client, file_database):
    """The question Phase 5 exists to answer, asked of real recorded rows."""
    for _ in range(3):
        client.post("/v1/chat/completions", json=CLEAN_REQUEST)

    events = recorded(file_database)

    assert sum(event.total_tokens for event in events) == 48
    assert {event.tenant.name for event in events} == {"Billing"}
