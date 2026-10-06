"""Phase 4: the proxy route, end to end with a fake provider behind it.

This file is where the product is either true or not. Everything else can
pass while this fails.

The fake provider records what it received, so these tests can assert the one
thing that matters: **what actually left the network.** A test that only
checks the gateway's own response would pass even if the card number were
forwarded anyway.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from gateway.engine.pipeline import build_pipeline
from gateway.engine.rules import Rules, parse_rules
from gateway.proxy.app import app, get_pipeline, get_upstream
from gateway.proxy.upstream import Upstream
from gateway.settings import Mode
from tests.stubs import TEST_PROVIDER_KEY, StubProvider

CARD = "my card is 4111 1111 1111 1111"

CARD_REQUEST = {
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": CARD}],
}


@pytest.fixture
def stub() -> StubProvider:
    return StubProvider()


@pytest.fixture
def client(stub: StubProvider, gateway_key: str):
    """The real app, with a fake provider behind it and a real key in front.

    `dependency_overrides` is FastAPI's seam for exactly this: the route asks
    for an Upstream, and in tests it gets one wired to the stub. The route is
    not modified, and does not know.

    The key is a genuine issued one, in a throwaway database -- these tests go
    through authentication rather than around it, because a test that skips
    the lock cannot notice when the lock stops working.
    """
    app.dependency_overrides[get_upstream] = lambda: Upstream(
        base_url="https://provider.test/v1",
        transport=stub.transport,
        api_key=TEST_PROVIDER_KEY,
        passthrough=False,
    )
    yield TestClient(app, headers={"Authorization": f"Bearer {gateway_key}"})
    app.dependency_overrides.clear()


def run_in(mode: Mode, rules: Rules | None = None) -> None:
    """Point the route at a pipeline in the given mode, for one test."""
    app.dependency_overrides[get_pipeline] = lambda: build_pipeline(mode, rules or Rules())


# --- enforce mode: the promise ------------------------------------------


def test_in_enforce_mode_the_card_never_leaves(client, stub):
    """The single most important test in the project.

    Not "the response looked right" -- what the provider actually received.
    """
    run_in(Mode.ENFORCE)

    client.post("/v1/chat/completions", json=CARD_REQUEST)

    assert stub.last_text == "my card is [CREDIT_CARD_1]"
    assert "4111" not in str(stub.last_body)


def test_in_enforce_mode_everything_else_in_the_request_survives(client, stub):
    run_in(Mode.ENFORCE)

    client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o-mini", "temperature": 0.2, "messages": CARD_REQUEST["messages"]},
    )

    assert stub.last_body["model"] == "gpt-4o-mini"
    assert stub.last_body["temperature"] == 0.2
    assert stub.last_body["messages"][0]["role"] == "user"


def test_the_image_in_a_multimodal_request_is_untouched(client, stub):
    run_in(Mode.ENFORCE)

    client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4o-mini",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "is priya@example.com here?"},
                        {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
                    ],
                }
            ],
        },
    )

    content = stub.last_body["messages"][0]["content"]
    assert content[0]["text"] == "is [EMAIL_1] here?"
    assert content[1]["image_url"]["url"].endswith("a.png")


# --- shadow mode: changes nothing, on purpose ---------------------------


def test_in_shadow_mode_the_original_is_forwarded(client, stub):
    """Uncomfortable but correct: in shadow mode the card *does* reach the
    provider. Shadow exists to observe without changing behaviour, so that an
    operator can see what enforce would have done before risking it. The
    headers still report what was found.
    """
    run_in(Mode.SHADOW)

    response = client.post("/v1/chat/completions", json=CARD_REQUEST)

    assert stub.last_text == CARD
    assert response.headers["x-gateway-decided"] == "redact"
    assert response.headers["x-gateway-action"] == "log"
    assert response.headers["x-gateway-categories"] == "credit_card"


# --- blocking ------------------------------------------------------------


def test_a_blocked_request_never_reaches_the_provider(client, stub):
    run_in(Mode.ENFORCE, parse_rules("categories:\n  credit_card: block\n"))

    response = client.post("/v1/chat/completions", json=CARD_REQUEST)

    assert not stub.called
    assert response.headers["x-gateway-action"] == "block"


def test_a_blocked_request_answers_in_a_shape_a_client_can_read(client, stub):
    """Not an HTTP error. A 403 would make their library raise and their user
    would see a spinner; this way the person reads what happened."""
    run_in(Mode.ENFORCE, parse_rules("categories:\n  credit_card: block\n"))

    response = client.post("/v1/chat/completions", json=CARD_REQUEST)
    body = response.json()

    assert response.status_code == 200
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["finish_reason"] == "content_filter"
    assert "credit card number" in body["choices"][0]["message"]["content"]
    assert "Nothing was sent to the provider" in body["choices"][0]["message"]["content"]


def test_a_refusal_never_repeats_the_value(client, stub):
    """The whole value, not the first four digits.

    This assertion originally looked for "4111" and failed roughly once every
    2,350 runs: a blocked response carries a random 16-character hex id, and
    "4111" turns up in random hex about that often. The full number, spaces
    and all, cannot appear by chance.
    """
    run_in(Mode.ENFORCE, parse_rules("categories:\n  credit_card: block\n"))

    response = client.post("/v1/chat/completions", json=CARD_REQUEST)

    assert "4111 1111 1111 1111" not in response.text


# --- ordinary traffic ----------------------------------------------------


def test_a_clean_request_passes_straight_through(client, stub):
    run_in(Mode.ENFORCE)

    response = client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "what is 2+2?"}]},
    )

    assert stub.last_text == "what is 2+2?"
    assert response.headers["x-gateway-action"] == "allow"
    assert response.headers["x-gateway-categories"] == ""
    assert response.json()["choices"][0]["message"]["role"] == "assistant"


def test_a_request_with_no_text_is_forwarded_as_it_is(client, stub):
    """Not a chat request, or a shape we don't recognise. The provider owns
    its own API; refusing what we don't recognise would break working apps."""
    run_in(Mode.ENFORCE)

    client.post("/v1/chat/completions", json={"model": "gpt-4o-mini"})

    assert stub.last_body == {"model": "gpt-4o-mini"}


def test_a_provider_key_in_passthrough_reaches_the_provider(client, stub, gateway_key):
    """Passthrough: the caller still holds their own provider key, and sends
    the gateway key separately so the gateway can tell who they are without
    touching the credential it was not given."""
    run_in(Mode.ENFORCE)
    app.dependency_overrides[get_upstream] = lambda: Upstream(
        transport=stub.transport, api_key=None, passthrough=True
    )
    provider_key = "Bearer sk-not-a-real-key-1111111111111111"

    client.post(
        "/v1/chat/completions",
        json=CARD_REQUEST,
        headers={"Authorization": provider_key, "X-Gateway-Key": gateway_key},
    )

    assert stub.requests[0].headers["authorization"] == provider_key


# --- when things go wrong ------------------------------------------------


def test_the_providers_rate_limit_is_passed_through(client):
    """Their answer, not ours. A caller who is rate limited needs to know
    that, and not a gateway error invented on top of it."""
    stub = StubProvider(status_code=429, body={"error": {"message": "slow down"}})
    app.dependency_overrides[get_upstream] = lambda: Upstream(
        transport=stub.transport, api_key=TEST_PROVIDER_KEY, passthrough=False
    )
    run_in(Mode.ENFORCE)

    response = client.post("/v1/chat/completions", json=CARD_REQUEST)

    assert response.status_code == 429
    assert response.json()["error"]["message"] == "slow down"


def test_a_slow_provider_becomes_a_504(client):
    stub = StubProvider(error=httpx.ConnectTimeout("too slow"))
    app.dependency_overrides[get_upstream] = lambda: Upstream(
        transport=stub.transport, api_key=TEST_PROVIDER_KEY, passthrough=False
    )
    run_in(Mode.ENFORCE)

    response = client.post("/v1/chat/completions", json=CARD_REQUEST)

    assert response.status_code == 504
    assert response.json()["error"]["type"] == "upstream_timeout"


def test_an_unreachable_provider_becomes_a_502(client):
    stub = StubProvider(error=httpx.ConnectError("no route to host"))
    app.dependency_overrides[get_upstream] = lambda: Upstream(
        transport=stub.transport, api_key=TEST_PROVIDER_KEY, passthrough=False
    )
    run_in(Mode.ENFORCE)

    response = client.post("/v1/chat/completions", json=CARD_REQUEST)

    assert response.status_code == 502
    assert response.json()["error"]["type"] == "upstream_unreachable"


def test_an_upstream_failure_still_reports_the_verdict(client):
    """The gateway did its job even though the provider didn't. The headers
    say so, which is what a log or a dashboard needs."""
    stub = StubProvider(error=httpx.ConnectError("no route to host"))
    app.dependency_overrides[get_upstream] = lambda: Upstream(
        transport=stub.transport, api_key=TEST_PROVIDER_KEY, passthrough=False
    )
    run_in(Mode.ENFORCE)

    response = client.post("/v1/chat/completions", json=CARD_REQUEST)

    assert response.headers["x-gateway-action"] == "redact"
    assert response.headers["x-gateway-categories"] == "credit_card"


def test_invalid_json_is_a_400(client):
    response = client.post(
        "/v1/chat/completions",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"


def test_a_json_body_that_is_not_an_object_is_a_400(client):
    response = client.post("/v1/chat/completions", json=["not", "an", "object"])

    assert response.status_code == 400
