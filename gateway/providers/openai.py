"""The OpenAI chat-completions shape.

A request looks like this, and most providers now copy it:

    {
      "model": "gpt-4o-mini",
      "messages": [
        {"role": "system",    "content": "You are a helpful assistant."},
        {"role": "user",      "content": "my card is 4111 1111 1111 1111"}
      ]
    }

Content can also be a list of parts, for images:

    "content": [
      {"type": "text", "text": "what is in this picture?"},
      {"type": "image_url", "image_url": {"url": "..."}}
    ]

Both forms are handled. Non-text parts are left alone -- we are not reading
images in V1, and pretending to would be worse than not doing it.

Two decisions worth knowing about:

**Every role is checked, not just "user".** A system prompt often carries the
company's own instructions and data, and a tool result can carry a whole
database row. All of it leaves the network, so all of it is checked.

**Locations are found once and reused.** `texts()` and `with_texts()` walk the
request through the same private helper, so the list that comes out and the
list that goes back in can never drift apart.
"""

import copy
import json
import time
import uuid
from typing import Any

from gateway.providers.base import Usage

_Location = tuple[Any, ...]
"""A path to one string inside the request body, e.g.
("messages", 0, "content") or ("messages", 1, "content", 2, "text")."""


def _locations(body: dict) -> list[_Location]:
    """Where every piece of checkable text sits, in a stable order.

    Deliberately forgiving: anything shaped unexpectedly is skipped rather
    than raised over. A proxy that rejects a request because it did not
    recognise one field is a proxy that breaks working applications, and the
    provider is the one entitled to validate its own API.
    """
    found: list[_Location] = []

    messages = body.get("messages")
    if not isinstance(messages, list):
        return found

    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue

        content = message.get("content")

        if isinstance(content, str):
            found.append(("messages", index, "content"))

        elif isinstance(content, list):
            for part_index, part in enumerate(content):
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    found.append(("messages", index, "content", part_index, "text"))

    return found


def _read(body: dict, location: _Location) -> str:
    value: Any = body
    for key in location:
        value = value[key]
    return value


def _write(body: dict, location: _Location, value: str) -> None:
    target: Any = body
    for key in location[:-1]:
        target = target[key]
    target[location[-1]] = value


class OpenAIProvider:
    """The adapter for OpenAI, and for everything that speaks its dialect --
    Groq, Together, OpenRouter, a local Ollama. The same shape, so the same
    adapter."""

    name = "openai"

    def texts(self, body: dict) -> list[str]:
        return [_read(body, location) for location in _locations(body)]

    def with_texts(self, body: dict, texts: list[str]) -> dict:
        locations = _locations(body)

        if len(texts) != len(locations):
            # Not a user error -- a bug in the calling code. Loudly, because
            # quietly putting text back in the wrong message would mean
            # sending one person's data in another person's request.
            raise ValueError(f"expected {len(locations)} texts for this request, got {len(texts)}")

        rebuilt = copy.deepcopy(body)
        for location, text in zip(locations, texts, strict=True):
            _write(rebuilt, location, text)

        return rebuilt

    def blocked_response(self, body: dict, message: str) -> dict:
        """A refusal that any OpenAI client can read without raising.

        `finish_reason` is "content_filter", which is part of OpenAI's own
        specification -- so a client that already handles filtered content
        handles this too, without knowing the gateway exists.

        Token counts are zero because nothing was sent anywhere. That is also
        the honest answer to "what did this request cost me".
        """
        return {
            "id": f"chatcmpl-gateway-{uuid.uuid4().hex[:16]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.get("model", "unknown"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": message},
                    "finish_reason": "content_filter",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    def blocked_stream(self, body: dict, message: str) -> bytes:
        """The same refusal, as server-sent events.

        A caller who asked for a stream has a client waiting for `data:`
        lines. Answering with a plain JSON body instead would leave their SDK
        parsing a stream that never comes, which is the opposite of the
        reason blocked_response exists at all: the refusal is supposed to be
        readable, not a different kind of crash.

        Sent as one content chunk, then a stop, then `[DONE]` -- the shortest
        thing that is a valid stream.
        """
        head = {
            "id": f"chatcmpl-gateway-{uuid.uuid4().hex[:16]}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": body.get("model", "unknown"),
        }

        events = [
            {**head, "choices": [{"index": 0, "delta": {"role": "assistant"}}]},
            {**head, "choices": [{"index": 0, "delta": {"content": message}}]},
            {**head, "choices": [{"index": 0, "delta": {}, "finish_reason": "content_filter"}]},
        ]

        lines = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
        return (lines + "data: [DONE]\n\n").encode()

    def wants_usage(self, body: dict) -> dict:
        """The request, asking the provider to report what it cost.

        Streaming replies carry no usage unless the request asked for it, so
        a gateway that forwarded the request untouched would record every
        streamed call as costing nothing -- the same misleading zero that
        made six failed requests look like free traffic in Phase 5.

        Only added when the caller did not set `stream_options` themselves.
        Someone who has an opinion about that field keeps it.
        """
        if "stream_options" in body:
            return body

        return {**body, "stream_options": {"include_usage": True}}

    def usage_from_stream(self, raw: bytes) -> Usage:
        """The token counts out of a streamed reply.

        A stream is `data:` lines, and the usage -- when it comes at all --
        is in a late one with no choices in it. Everything is forgiving for
        the same reason as `usage`: by the time this runs the caller already
        has their answer, so an unreadable chunk means an unknown cost and
        not a failure.
        """
        for line in reversed(raw.decode(errors="replace").splitlines()):
            if not line.startswith("data: "):
                continue

            payload = line.removeprefix("data: ").strip()
            if payload == "[DONE]":
                continue

            try:
                event = json.loads(payload)
            except (json.JSONDecodeError, ValueError):
                continue

            if isinstance(event, dict) and isinstance(event.get("usage"), dict):
                return self.usage(payload.encode())

        return Usage()

    def usage(self, raw: bytes) -> Usage:
        """The token counts out of a reply, in OpenAI's shape:

            {"usage": {"prompt_tokens": 9, "completion_tokens": 7, ...}}

        Forgiving at every step, and deliberately so. This runs after the
        caller's answer is already in hand, so there is nothing left to win by
        objecting: a reply that is not JSON, or has no usage, or reports it in
        some shape we have not seen, means the cost is unknown. It does not
        mean the request failed.
        """
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            return Usage()

        counts = payload.get("usage") if isinstance(payload, dict) else None

        if not isinstance(counts, dict):
            return Usage()

        def number(name: str) -> int | None:
            value = counts.get(name)
            # bool is an int in Python, and `True` as a token count would be a
            # strange thing to store.
            return value if isinstance(value, int) and not isinstance(value, bool) else None

        return Usage(
            prompt_tokens=number("prompt_tokens"),
            completion_tokens=number("completion_tokens"),
            total_tokens=number("total_tokens"),
        )
