"""Phase 4: the outgoing side.

Everything here uses the stub provider, so nothing touches the network. These
tests are about four questions:

    does the request we meant to send actually get sent?
    do we forward the headers we should, and only those?
    is the provider's own answer passed through untouched?
    when the provider fails, do we say so without leaking anything?
"""

import httpx
import pytest

from gateway.proxy.upstream import Upstream, UpstreamError, forwarded_headers
from tests.stubs import CANNED_REPLY, TEST_PROVIDER_KEY, StubProvider

BODY = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]}


def an_upstream(stub: StubProvider) -> Upstream:
    """Configured the way a real deployment is: the gateway holds the
    provider key. Since step 4 an unconfigured Upstream refuses to send at
    all, which is why even tests about timeouts have to be set up properly."""
    return Upstream(
        base_url="https://provider.test/v1",
        transport=stub.transport,
        api_key=TEST_PROVIDER_KEY,
        passthrough=False,
    )


async def test_the_body_arrives_exactly_as_given():
    stub = StubProvider()

    await an_upstream(stub).send("/chat/completions", BODY, {})

    assert stub.last_body == BODY


async def test_the_path_is_joined_to_the_base_url():
    stub = StubProvider()

    await an_upstream(stub).send("/chat/completions", BODY, {})

    assert str(stub.requests[0].url) == "https://provider.test/v1/chat/completions"


async def test_the_providers_reply_comes_back():
    stub = StubProvider(reply="Four.")

    response = await an_upstream(stub).send("/chat/completions", BODY, {})

    assert response.status_code == 200
    assert b"Four." in response.body
    assert response.media_type.startswith("application/json")


async def test_the_providers_own_errors_are_passed_through():
    """A 429 is the provider's answer, not a gateway failure. A caller being
    rate limited needs to know that, not a 502 invented on top of it."""
    stub = StubProvider(status_code=429, body={"error": {"message": "slow down"}})

    response = await an_upstream(stub).send("/chat/completions", BODY, {})

    assert response.status_code == 429
    assert b"slow down" in response.body


async def test_a_timeout_becomes_an_upstream_error():
    """The test a real provider could never give us on demand."""
    stub = StubProvider(error=httpx.ConnectTimeout("too slow"))

    with pytest.raises(UpstreamError, match="did not answer in time"):
        await an_upstream(stub).send("/chat/completions", BODY, {})


async def test_an_unreachable_provider_becomes_an_upstream_error():
    stub = StubProvider(error=httpx.ConnectError("no route to host"))

    with pytest.raises(UpstreamError, match="could not be reached"):
        await an_upstream(stub).send("/chat/completions", BODY, {})


async def test_the_error_never_quotes_the_request():
    """Same rule as the pipeline's: an exception message can quote the text it
    was handed, and the text is what we are protecting."""
    secret = "my card is 4111 1111 1111 1111"
    stub = StubProvider(error=httpx.ConnectError(f"failed sending {secret}"))

    with pytest.raises(UpstreamError) as caught:
        await an_upstream(stub).send(
            "/chat/completions",
            {"messages": [{"role": "user", "content": secret}]},
            {},
        )

    assert "4111" not in str(caught.value)


def test_only_allowlisted_headers_are_forwarded():
    """Host would name the gateway, Content-Length is wrong the moment text is
    redacted, and a cookie has no business leaving the network."""
    sent = forwarded_headers(
        {
            "Authorization": "Bearer sk-not-a-real-key-0000",
            "Content-Type": "application/json",
            "OpenAI-Beta": "assistants=v2",
            "Host": "127.0.0.1:8080",
            "Content-Length": "412",
            "Cookie": "session=abc123",
            "X-Forwarded-For": "10.0.0.1",
        }
    )

    assert set(sent) == {"Authorization", "Content-Type", "OpenAI-Beta"}


async def test_in_passthrough_the_callers_key_is_forwarded_untouched():
    """Passthrough is for a team mid-migration: they still hold their own
    provider key, and the gateway does not touch a credential it was not
    given. Normal mode replaces it -- see test_provider_key.py."""
    stub = StubProvider()
    key = "Bearer sk-not-a-real-key-1111111111111111"

    upstream = Upstream(transport=stub.transport, api_key=None, passthrough=True)
    await upstream.send("/chat/completions", BODY, {"Authorization": key})

    assert stub.requests[0].headers["authorization"] == key


async def test_the_stub_records_what_actually_left():
    """The assertion that matters most in the whole project, proved here on
    the stub before Step 5 relies on it: what we sent is inspectable."""
    stub = StubProvider()

    await an_upstream(stub).send(
        "/chat/completions",
        {"messages": [{"role": "user", "content": "my card is [CREDIT_CARD_1]"}]},
        {},
    )

    assert stub.last_text == "my card is [CREDIT_CARD_1]"
    assert CANNED_REPLY  # the stub's reply is a fixed string, not a live model
