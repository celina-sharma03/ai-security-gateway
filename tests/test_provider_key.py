"""Phase 5: the gateway holds the provider key.

This is the step that turns the gateway from advice into a control. If
developers hold the provider key, anyone who finds the gateway inconvenient
points their code straight at the provider. If the gateway holds the only key,
routing around it means having no key at all.

So the tests are about exactly one question: **which credential leaves this
process?**
"""

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from gateway.proxy.app import app, get_upstream
from gateway.proxy.upstream import Upstream, UpstreamNotConfigured
from gateway.settings import settings
from tests.stubs import StubProvider

REAL_KEY = SecretStr("sk-not-a-real-key-0000000000000000000000000")
CALLER_KEY = "Bearer sk-not-a-real-key-1111111111111111111"

BODY = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]}
ASK = BODY


# --- the outgoing side on its own ---------------------------------------


async def test_the_gateways_key_is_the_one_that_leaves():
    stub = StubProvider()
    upstream = Upstream(transport=stub.transport, api_key=REAL_KEY, passthrough=False)

    await upstream.send("/chat/completions", BODY, {})

    assert stub.requests[0].headers["authorization"] == f"Bearer {REAL_KEY.get_secret_value()}"


async def test_whatever_the_caller_sent_is_replaced():
    """Not merged, not preferred -- replaced. In normal mode exactly one
    credential can leave this process, and it is the configured one."""
    stub = StubProvider()
    upstream = Upstream(transport=stub.transport, api_key=REAL_KEY, passthrough=False)

    await upstream.send("/chat/completions", BODY, {"Authorization": CALLER_KEY})

    assert stub.requests[0].headers["authorization"] != CALLER_KEY
    assert CALLER_KEY not in str(dict(stub.requests[0].headers))


async def test_passthrough_leaves_the_callers_key_alone():
    stub = StubProvider()
    upstream = Upstream(transport=stub.transport, api_key=None, passthrough=True)

    await upstream.send("/chat/completions", BODY, {"Authorization": CALLER_KEY})

    assert stub.requests[0].headers["authorization"] == CALLER_KEY


async def test_passthrough_does_not_substitute_even_when_a_key_is_configured():
    """Passthrough means the caller's credential is theirs. Quietly swapping
    it would be a surprise in the one direction nobody wants surprises."""
    stub = StubProvider()
    upstream = Upstream(transport=stub.transport, api_key=REAL_KEY, passthrough=True)

    await upstream.send("/chat/completions", BODY, {"Authorization": CALLER_KEY})

    assert stub.requests[0].headers["authorization"] == CALLER_KEY


async def test_no_key_and_no_passthrough_is_an_error_not_a_silent_request():
    """Forwarding unauthenticated would make the provider answer 401, and
    whoever read that 401 would spend an afternoon looking at their own key
    rather than at a gateway that never had one."""
    stub = StubProvider()
    upstream = Upstream(transport=stub.transport, api_key=None, passthrough=False)

    with pytest.raises(UpstreamNotConfigured, match="GATEWAY_UPSTREAM_API_KEY"):
        await upstream.send("/chat/completions", BODY, {})

    assert not stub.called


# --- through the whole app ----------------------------------------------


@pytest.fixture
def stub() -> StubProvider:
    return StubProvider()


@pytest.fixture
def client(stub: StubProvider, gateway_key: str):
    yield TestClient(app, headers={"Authorization": f"Bearer {gateway_key}"})
    app.dependency_overrides.clear()


def use_upstream(stub: StubProvider, **kwargs) -> None:
    app.dependency_overrides[get_upstream] = lambda: Upstream(transport=stub.transport, **kwargs)


def test_a_developer_never_needs_the_real_key(client, stub, gateway_key):
    """The whole point, end to end: the caller sends only a gateway key, and
    the provider receives only the real one."""
    use_upstream(stub, api_key=REAL_KEY, passthrough=False)

    response = client.post("/v1/chat/completions", json=ASK)

    assert response.status_code == 200
    assert stub.requests[0].headers["authorization"] == f"Bearer {REAL_KEY.get_secret_value()}"
    assert gateway_key not in str(dict(stub.requests[0].headers))


def test_a_misconfigured_gateway_says_so_clearly(client, stub):
    use_upstream(stub, api_key=None, passthrough=False)

    response = client.post("/v1/chat/completions", json=ASK)

    assert response.status_code == 500
    assert response.json()["error"]["type"] == "gateway_misconfigured"
    assert "GATEWAY_UPSTREAM_API_KEY" in response.json()["error"]["message"]
    assert not stub.called


def test_the_verdict_is_still_reported_when_misconfigured(client, stub):
    """The checks ran and found something. That stays true even though the
    request could not be sent."""
    use_upstream(stub, api_key=None, passthrough=False)

    response = client.post(
        "/v1/chat/completions",
        json={"model": "x", "messages": [{"role": "user", "content": "card 4111 1111 1111 1111"}]},
    )

    assert response.headers["x-gateway-categories"] == "credit_card"


# --- not leaking it ------------------------------------------------------


def test_the_key_does_not_appear_when_the_settings_are_printed():
    """SecretStr, so a startup banner, a log line, an exception or a debugger
    shows `**********`. Reaching the value takes .get_secret_value(), which is
    a thing you have to mean."""
    secret = SecretStr("sk-not-a-real-key-9999999999999999999999999")

    assert "sk-not-a-real-key" not in repr(secret)
    assert "sk-not-a-real-key" not in str(secret)
    assert secret.get_secret_value() == "sk-not-a-real-key-9999999999999999999999999"


def test_the_settings_object_hides_it_too(monkeypatch):
    monkeypatch.setattr(settings, "upstream_api_key", REAL_KEY)

    assert REAL_KEY.get_secret_value() not in repr(settings)
    assert REAL_KEY.get_secret_value() not in str(settings.model_dump())


async def test_an_upstream_failure_never_quotes_the_key():
    """An exception message is a log line waiting to happen."""
    stub = StubProvider(error=httpx.ConnectError("no route to host"))
    upstream = Upstream(transport=stub.transport, api_key=REAL_KEY, passthrough=False)

    with pytest.raises(Exception) as caught:
        await upstream.send("/chat/completions", BODY, {})

    assert REAL_KEY.get_secret_value() not in str(caught.value)
