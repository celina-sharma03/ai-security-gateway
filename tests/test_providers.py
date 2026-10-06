"""Phase 4: the provider adapter.

The adapter is the only part of the proxy that knows a provider's request
shape, so these tests are about one thing: can text be taken out and put back
without disturbing anything else in the request?

The dangerous failure here is silent. Putting text back in the wrong place
would send one person's data inside another person's request, and nothing
would crash.
"""

import pytest

from gateway.providers.openai import OpenAIProvider

provider = OpenAIProvider()


def a_request(*contents: object) -> dict:
    return {
        "model": "gpt-4o-mini",
        "temperature": 0.7,
        "messages": [{"role": "user", "content": content} for content in contents],
    }


def test_texts_are_found_in_order():
    body = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "my card is 4111 1111 1111 1111"},
            {"role": "assistant", "content": "I cannot help with that."},
        ],
    }

    assert provider.texts(body) == [
        "You are helpful.",
        "my card is 4111 1111 1111 1111",
        "I cannot help with that.",
    ]


def test_every_role_is_checked_not_only_user():
    """A system prompt carries the company's own instructions, and a tool
    result can carry a database row. All of it leaves the network."""
    body = {
        "messages": [
            {"role": "system", "content": "internal: ops@example.com"},
            {"role": "tool", "content": "row: priya@example.com"},
        ]
    }

    assert len(provider.texts(body)) == 2


def test_text_parts_of_a_multimodal_message_are_found():
    body = a_request(
        [
            {"type": "text", "text": "what is in this picture?"},
            {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
        ]
    )

    assert provider.texts(body) == ["what is in this picture?"]


def test_replacing_text_leaves_everything_else_alone():
    body = a_request("my card is 4111 1111 1111 1111")

    rebuilt = provider.with_texts(body, ["my card is [CREDIT_CARD_1]"])

    assert rebuilt["messages"][0]["content"] == "my card is [CREDIT_CARD_1]"
    assert rebuilt["messages"][0]["role"] == "user"
    assert rebuilt["model"] == "gpt-4o-mini"
    assert rebuilt["temperature"] == 0.7


def test_the_original_request_is_never_mutated():
    """The caller's body is theirs. A proxy that edits what it was handed is
    a proxy nobody can debug."""
    body = a_request("my card is 4111 1111 1111 1111")

    provider.with_texts(body, ["replaced"])

    assert body["messages"][0]["content"] == "my card is 4111 1111 1111 1111"


def test_replacing_a_multimodal_message_keeps_the_image():
    body = a_request(
        [
            {"type": "text", "text": "is priya@example.com in this?"},
            {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
        ]
    )

    rebuilt = provider.with_texts(body, ["is [EMAIL_1] in this?"])

    assert rebuilt["messages"][0]["content"][0]["text"] == "is [EMAIL_1] in this?"
    assert rebuilt["messages"][0]["content"][1]["image_url"]["url"].endswith("a.png")


def test_the_wrong_number_of_texts_is_an_error():
    """A bug in the calling code, not a user mistake. Quietly putting text
    back in the wrong message is the one failure here that would not crash."""
    body = a_request("one", "two")

    with pytest.raises(ValueError, match="expected 2 texts"):
        provider.with_texts(body, ["only one"])


def test_a_request_with_nothing_to_check_is_handled():
    """Malformed, or simply not a chat request. The provider validates its own
    API; the gateway's job is not to reject what it doesn't recognise."""
    assert provider.texts({}) == []
    assert provider.texts({"messages": "not a list"}) == []
    assert provider.texts({"messages": [{"role": "user"}]}) == []


def test_a_blocked_response_looks_like_a_real_one():
    """Any OpenAI client must be able to read this without raising."""
    body = a_request("my card is 4111 1111 1111 1111")

    response = provider.blocked_response(body, "Blocked: it contained a card number.")

    assert response["object"] == "chat.completion"
    assert response["model"] == "gpt-4o-mini"
    assert response["choices"][0]["message"]["role"] == "assistant"
    assert "card number" in response["choices"][0]["message"]["content"]
    assert response["choices"][0]["finish_reason"] == "content_filter"
    assert response["usage"]["total_tokens"] == 0


def test_a_blocked_response_never_contains_the_blocked_value():
    """The whole number, not a four-digit fragment: the response carries a
    random hex id, and "4111" appears in random hex about once in 2,350
    responses. See the note in test_proxy_route.py."""
    body = a_request("my card is 4111 1111 1111 1111")

    response = provider.blocked_response(body, "Blocked: it contained a card number.")

    assert "4111 1111 1111 1111" not in str(response)


# --- what it cost --------------------------------------------------------


def test_usage_is_read_from_the_reply():
    raw = b'{"usage": {"prompt_tokens": 9, "completion_tokens": 7, "total_tokens": 16}}'

    counted = provider.usage(raw)

    assert counted.prompt_tokens == 9
    assert counted.completion_tokens == 7
    assert counted.total_tokens == 16


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"not json at all",
        b"<html>502 Bad Gateway</html>",
        b"[1, 2, 3]",
        b'{"choices": []}',
        b'{"usage": null}',
        b'{"usage": "unavailable"}',
    ],
    ids=lambda raw: repr(raw[:24]),
)
def test_an_unreadable_reply_means_unknown_not_broken(raw):
    """This runs after the caller's answer is already in hand. There is
    nothing left to win by objecting to the shape of it."""
    counted = provider.usage(raw)

    assert counted.prompt_tokens is None
    assert counted.total_tokens is None


def test_a_missing_field_is_unknown_rather_than_zero():
    """None means "we don't know". Zero would mean "they told us it cost
    nothing", which is a different claim."""
    counted = provider.usage(b'{"usage": {"prompt_tokens": 9}}')

    assert counted.prompt_tokens == 9
    assert counted.completion_tokens is None


def test_a_boolean_is_not_a_token_count():
    """bool is an int in Python, so `true` would otherwise be stored as 1."""
    assert provider.usage(b'{"usage": {"prompt_tokens": true}}').prompt_tokens is None
