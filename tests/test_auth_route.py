"""Phase 5: the proxy requires a gateway key.

The test that matters most is the one about what leaves: a gateway key must
never reach the provider. Sending `gw_live_...` to OpenAI would write a
working credential for this gateway into a third party's logs -- the same
class of mistake the whole project exists to prevent, made by the project
itself.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gateway.auth import keys as keyring
from gateway.proxy.app import app, get_upstream
from gateway.proxy.upstream import Upstream
from gateway.storage.models import ApiKey
from tests.stubs import TEST_PROVIDER_KEY, StubProvider

ASK = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "what is 2+2?"}]}


@pytest.fixture
def stub() -> StubProvider:
    return StubProvider()


@pytest.fixture
def client(stub: StubProvider, file_database):
    app.dependency_overrides[get_upstream] = lambda: Upstream(
        transport=stub.transport, api_key=TEST_PROVIDER_KEY, passthrough=False
    )
    yield TestClient(app)
    app.dependency_overrides.clear()


# --- refusing ------------------------------------------------------------


def test_no_key_is_refused(client, stub):
    response = client.post("/v1/chat/completions", json=ASK)

    assert response.status_code == 401
    assert response.json()["error"]["type"] == "invalid_api_key"
    assert response.headers["www-authenticate"] == "Bearer"
    assert not stub.called


def test_the_refusal_says_what_to_do(client):
    """A 401 that only says "unauthorized" costs someone twenty minutes."""
    message = client.post("/v1/chat/completions", json=ASK).json()["error"]["message"]

    assert "Authorization: Bearer gw_live_" in message
    assert "X-Gateway-Key" in message


def test_an_unknown_key_is_refused(client, stub):
    response = client.post(
        "/v1/chat/completions",
        json=ASK,
        headers={"Authorization": f"Bearer {keyring.generate()}"},
    )

    assert response.status_code == 401
    assert not stub.called


def test_a_revoked_key_is_refused(client, stub, gateway_key, file_database):
    """And is refused exactly like an unknown one. The caller learns nothing
    from the difference; the gateway's own records keep it."""

    async def revoke() -> None:
        engine = create_async_engine(file_database)
        async with async_sessionmaker(engine)() as session:
            await keyring.revoke(session, keyring.visible_part(gateway_key))
        await engine.dispose()

    asyncio.run(revoke())

    response = client.post(
        "/v1/chat/completions", json=ASK, headers={"Authorization": f"Bearer {gateway_key}"}
    )

    assert response.status_code == 401
    assert not stub.called


def test_nonsense_in_the_header_is_refused(client, stub):
    for value in ("", "Bearer", "Bearer hunter2", "gw_live_but_not_bearer"):
        response = client.post("/v1/chat/completions", json=ASK, headers={"Authorization": value})
        assert response.status_code == 401, value

    assert not stub.called


# --- allowing ------------------------------------------------------------


def test_a_real_key_gets_through(client, stub, gateway_key):
    response = client.post(
        "/v1/chat/completions", json=ASK, headers={"Authorization": f"Bearer {gateway_key}"}
    )

    assert response.status_code == 200
    assert stub.called


def test_the_answer_says_whose_traffic_it_was(client, gateway_key):
    response = client.post(
        "/v1/chat/completions", json=ASK, headers={"Authorization": f"Bearer {gateway_key}"}
    )

    assert response.headers["x-gateway-tenant"] == "Billing"


# --- the one that matters ------------------------------------------------


def test_the_gateway_key_never_reaches_the_provider(client, stub, gateway_key):
    """Forwarding it would write a working credential for this gateway into a
    third party's logs.

    Since step 4 there *is* an Authorization header going out -- the gateway's
    own provider key, put there on the way. What matters is that it is that
    one and not the caller's.
    """
    client.post(
        "/v1/chat/completions", json=ASK, headers={"Authorization": f"Bearer {gateway_key}"}
    )

    sent = stub.requests[0]

    assert sent.headers["authorization"] == f"Bearer {TEST_PROVIDER_KEY.get_secret_value()}"
    assert gateway_key not in str(dict(sent.headers))
    assert gateway_key not in sent.content.decode()


def test_the_gateway_key_is_not_forwarded_in_passthrough_either(client, stub, gateway_key):
    """Here the caller does send a provider key, which goes on untouched --
    but X-Gateway-Key is ours and stops here."""
    app.dependency_overrides[get_upstream] = lambda: Upstream(
        transport=stub.transport, api_key=None, passthrough=True
    )

    client.post(
        "/v1/chat/completions",
        json=ASK,
        headers={
            "Authorization": "Bearer sk-not-a-real-key-0000000000000000",
            "X-Gateway-Key": gateway_key,
        },
    )

    sent = stub.requests[0]

    assert sent.headers["authorization"] == "Bearer sk-not-a-real-key-0000000000000000"
    assert gateway_key not in str(dict(sent.headers))


# --- what stays open -----------------------------------------------------


def test_check_health_and_root_need_no_key(client):
    """The three endpoints that contact no provider and spend nothing. /check
    is the demo: requiring a key would mean nobody can try the thing before
    deciding to run it. They belong behind auth in a deployment that wants
    them private, which is a setting for later, not a default now."""
    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 200
    assert client.post("/check", json={"text": "my card is 4111 1111 1111 1111"}).status_code == 200


# --- last used -----------------------------------------------------------


def test_using_a_key_records_that_it_was_used(client, gateway_key, file_database):
    """So "is anyone still using this key, or can I revoke it?" has an answer."""
    client.post(
        "/v1/chat/completions", json=ASK, headers={"Authorization": f"Bearer {gateway_key}"}
    )

    async def last_used():
        engine = create_async_engine(file_database)
        try:
            async with async_sessionmaker(engine)() as session:
                return (await session.execute(select(ApiKey))).scalar_one().last_used_at
        finally:
            await engine.dispose()

    assert asyncio.run(last_used()) is not None
