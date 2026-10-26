"""Phase 9: putting the real values back into a reply.

Redaction without restoration gives you a draft email addressed to
`[EMAIL_1]` -- useless, annoying, and annoying security tools get switched
off. The provider never sees the real address; the person asking never sees
anything else.

The hard half is streaming. A reply arrives in fragments and a placeholder
can be split across two of them, so the substitution has to be stateful: a
fragment that might be the start of a placeholder is held back and goes out
joined to the next one.
"""

import json as _json

import pytest
from fastapi.testclient import TestClient

from gateway.engine.checks.redaction import LONGEST_PLACEHOLDER, Restorer, restore
from gateway.engine.pipeline import build_pipeline
from gateway.engine.rules import Rules
from gateway.proxy.app import app, get_pipeline, get_upstream
from gateway.proxy.upstream import Upstream
from gateway.settings import Mode
from tests.stubs import TEST_PROVIDER_KEY, StubProvider

MAPPING = {"[EMAIL_1]": "priya@acme.com", "[CREDIT_CARD_1]": "4111 1111 1111 1111"}


def streamed(chunks: list[str], mapping: dict[str, str] | None = None) -> str:
    """What the person ends up seeing, given a reply arriving in pieces."""
    restorer = Restorer(MAPPING if mapping is None else mapping)
    return "".join(restorer.feed(c) for c in chunks) + restorer.flush()


# --- All at once --------------------------------------------------------


def test_a_placeholder_becomes_the_real_value():
    assert restore("send it to [EMAIL_1]", MAPPING) == "send it to priya@acme.com"


def test_text_with_no_placeholders_is_untouched():
    assert restore("nothing to see", MAPPING) == "nothing to see"


def test_an_empty_mapping_changes_nothing():
    assert restore("send it to [EMAIL_1]", {}) == "send it to [EMAIL_1]"


# --- In pieces ----------------------------------------------------------


def test_a_placeholder_split_across_chunks_is_restored():
    """The case the whole class exists for."""
    assert streamed(["I'll email [EMA", "IL_1] today"]) == "I'll email priya@acme.com today"


def test_a_placeholder_split_into_three_is_restored():
    assert streamed(["[", "EMAIL", "_1]"]) == "priya@acme.com"


def test_a_placeholder_whole_in_one_chunk_is_restored():
    assert streamed(["hello [EMAIL_1] there"]) == "hello priya@acme.com there"


def test_two_placeholders_in_one_stream():
    seen = streamed(["pay [CREDIT_C", "ARD_1] and tell [EMAIL", "_1]"])
    assert seen == "pay 4111 1111 1111 1111 and tell priya@acme.com"


def test_the_person_never_sees_a_partial_placeholder():
    """Not just the final text -- no *intermediate* state may contain one."""
    restorer = Restorer(MAPPING)
    for chunk in ["I'll email [EMA", "IL_1] today"]:
        sent = restorer.feed(chunk)
        assert "[EMA" not in sent, f"a broken placeholder reached the screen: {sent!r}"


# --- Not everything in brackets is a placeholder ------------------------


def test_a_citation_does_not_stall_the_stream():
    """A model writing `[1]` must not hold the reply hostage."""
    assert streamed(["see [1] for more"]) == "see [1] for more"


def test_an_unclosed_bracket_is_given_up_on():
    """Held forever, a stray `[` would stop the stream dead. After forty
    characters it was plainly not a placeholder."""
    tail = "[" + "x" * (LONGEST_PLACEHOLDER + 5)
    assert streamed([f"text {tail}"]) == f"text {tail}"


def test_an_unclosed_bracket_at_the_end_is_still_sent():
    """Swallowing the model's own text would be worse than a stray bracket."""
    assert streamed(["the array is ["]) == "the array is ["


def test_a_bracket_that_closes_later_is_waited_for():
    assert streamed(["the array is [", "1, 2, 3]"]) == "the array is [1, 2, 3]"


# --- Nothing to do ------------------------------------------------------


def test_with_no_mapping_chunks_pass_straight_through():
    """The common case by a wide margin: nothing was redacted, so nothing
    is held back and streaming is untouched."""
    restorer = Restorer({})
    assert restorer.feed("I'll email [EMA") == "I'll email [EMA"
    assert restorer.pending == ""


@pytest.mark.parametrize(
    "chunks",
    [
        ["plain text"],
        ["several", " chunks", " of", " plain", " text"],
        [""],
    ],
)
def test_text_survives_being_cut_anywhere(chunks):
    assert streamed(chunks, {}) == "".join(chunks)


# --- Through the whole gateway ------------------------------------------
# Phase 9's exit criterion: "a redacted email round-trips and comes back
# intact". The provider must see the placeholder and the caller must see
# the address, in the same request.


ASK = "Email priya@acme.com about the invoice"


def gateway(reply: str, gateway_key: str, mode: Mode = Mode.ENFORCE):
    stub = StubProvider(reply=reply)
    app.dependency_overrides[get_upstream] = lambda: Upstream(
        transport=stub.transport, api_key=TEST_PROVIDER_KEY, passthrough=False
    )
    app.dependency_overrides[get_pipeline] = lambda: build_pipeline(mode, Rules())
    return TestClient(app, headers={"Authorization": f"Bearer {gateway_key}"}), stub


def test_the_round_trip(gateway_key):
    """The exit criterion, in one test.

    The provider is given a placeholder and answers with it. The person who
    asked gets their colleague's address back and never learns the gateway
    was involved.
    """
    client, stub = gateway("I'll email [EMAIL_1] today", gateway_key)
    try:
        reply = client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": ASK}]},
        ).json()
    finally:
        app.dependency_overrides.clear()

    assert "priya@acme.com" not in stub.last_text, "the real address left the network"
    assert "[EMAIL_1]" in stub.last_text

    answered = reply["choices"][0]["message"]["content"]
    assert answered == "I'll email priya@acme.com today"


def test_the_round_trip_while_streaming(gateway_key):
    """The same, in pieces. The stub sends the reply a word at a time, so
    `[EMAIL_1]` is split across events."""
    client, stub = gateway("I'll email [EMAIL_1] today", gateway_key)
    try:
        body = client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o-mini",
                "stream": True,
                "messages": [{"role": "user", "content": ASK}],
            },
        ).text
    finally:
        app.dependency_overrides.clear()

    assert "priya@acme.com" not in stub.last_text

    seen = ""
    for chunk in body.split("\n\n"):
        payload = chunk.strip().removeprefix("data: ")
        if not payload or payload == "[DONE]":
            continue
        for choice in _json.loads(payload).get("choices") or []:
            seen += choice.get("delta", {}).get("content", "")

    assert "[EMAIL_1]" not in seen, "the caller was shown a placeholder"
    assert "priya@acme.com" in seen


def test_shadow_mode_restores_nothing_because_nothing_was_replaced(gateway_key):
    """In shadow mode the real address went to the provider, so there is no
    mapping and nothing to put back. The reply is passed through as it came."""
    client, stub = gateway("I'll email priya@acme.com today", gateway_key, mode=Mode.SHADOW)
    try:
        reply = client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": ASK}]},
        ).json()
    finally:
        app.dependency_overrides.clear()

    assert "priya@acme.com" in stub.last_text, "shadow mode changed the request"
    assert reply["choices"][0]["message"]["content"] == "I'll email priya@acme.com today"


def test_a_reply_that_mentions_no_placeholder_is_untouched(gateway_key):
    client, _ = gateway("Sure, I can help with that.", gateway_key)
    try:
        reply = client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": ASK}]},
        ).json()
    finally:
        app.dependency_overrides.clear()

    assert reply["choices"][0]["message"]["content"] == "Sure, I can help with that."
