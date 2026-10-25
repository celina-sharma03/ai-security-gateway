"""A fake provider, for tests.

Not a test file -- a tool the tests use. It stands in for OpenAI (or Groq, or
anything speaking the same dialect) and answers instantly, offline, the same
way every time.

Why not call the real provider in tests:

  The suite runs in about a second. A real call is half a second to two
  seconds, every time, and tests that take a minute stop being run.

  A model never answers the same way twice. A test needs a fixed answer, or it
  is testing the provider's mood.

  Tests must pass with no internet and no API key. The moment the suite needs
  a secret, it fails for anyone who hasn't got one.

  And the important one: **a real provider cannot be made to fail on command.**
  The tests that matter most are what happens when the provider times out,
  returns 429, or dies halfway. With a stub, that is one argument.

The stub records every request it receives, which is how a test can prove the
*redacted* text was what actually left the network -- the single most important
assertion in the whole project.
"""

import json

import httpx
from pydantic import SecretStr

CANNED_REPLY = "Hello! How can I help?"

#: What the stub reports a request cost. The same numbers whether the
#: reply came in one piece or as a stream, so a test can assert that
#: streaming does not quietly lose the count.
STUB_USAGE = {"prompt_tokens": 9, "completion_tokens": 7, "total_tokens": 16}


def chat_completion(content: str = CANNED_REPLY, model: str = "gpt-4o-mini") -> dict:
    """A reply in OpenAI's chat-completions shape, trimmed to what matters."""
    return {
        "id": "chatcmpl-stub-0000000000",
        "object": "chat.completion",
        "created": 1_760_000_000,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 9, "completion_tokens": 7, "total_tokens": 16},
    }


class StubProvider:
    """A provider that answers however the test needs it to.

        stub = StubProvider()                      # a normal 200 reply
        stub = StubProvider(status_code=429, body={"error": "slow down"})
        stub = StubProvider(error=httpx.ConnectTimeout("too slow"))

    Then `Upstream(transport=stub.transport)` and the code under test can't
    tell the difference.
    """

    def __init__(
        self,
        *,
        reply: str = CANNED_REPLY,
        status_code: int = 200,
        body: dict | None = None,
        error: Exception | None = None,
    ) -> None:
        self.reply = reply
        self.status_code = status_code
        self.body = body
        self.error = error
        self.requests: list[httpx.Request] = []

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        # Recorded before anything else, so a test can still inspect what was
        # sent even when the stub is configured to fail.
        self.requests.append(request)

        if self.error is not None:
            raise self.error

        # A provider answers a streaming request with server-sent events, so
        # the stub reads the flag the same way the real one does rather than
        # being told separately by the test. That way a test cannot ask for a
        # stream and get a lump without noticing.
        asked_to_stream = bool(self.last_body.get("stream")) if request.content else False

        if asked_to_stream and self.body is None and self.status_code == 200:
            wants_usage = bool((self.last_body.get("stream_options") or {}).get("include_usage"))
            return httpx.Response(
                200,
                content=completion_stream(
                    self.reply,
                    usage=STUB_USAGE if wants_usage else None,
                ),
                headers={"content-type": "text/event-stream"},
            )

        return httpx.Response(
            self.status_code,
            json=self.body if self.body is not None else chat_completion(self.reply),
        )

    @property
    def called(self) -> bool:
        return bool(self.requests)

    @property
    def last_body(self) -> dict:
        """The JSON of the most recent request, as the provider would read it."""
        return json.loads(self.requests[-1].content)

    @property
    def last_text(self) -> str:
        """Every message's content, joined -- for asserting what left as one
        string, when a test doesn't care which message it was in."""
        messages = self.last_body.get("messages", [])
        return "\n".join(
            message["content"] for message in messages if isinstance(message.get("content"), str)
        )


TEST_PROVIDER_KEY = SecretStr("sk-not-a-real-key-0000000000000000000000000")
"""The provider key the gateway holds, in tests.

Since Phase 5 step 4 an Upstream in normal mode refuses to send a request
without one -- so tests that are about something else entirely (forwarding,
timeouts, redaction) still have to be configured like the real thing. That is
the right way round: the alternative is a suite where the gateway is
unconfigured everywhere and nobody notices.
"""


# --- Streaming -----------------------------------------------------------
# A provider streams a chat completion as server-sent events: one `data:`
# line per chunk, each carrying a fragment of the reply, ending with a
# literal `data: [DONE]`. The gateway has to forward those as they arrive
# rather than collecting them, which is the whole of Phase 8.


def chunk(
    content: str | None = None,
    *,
    finish: str | None = None,
    usage: dict | None = None,
    model: str = "gpt-4o-mini",
) -> str:
    """One `data:` event, in the shape a provider sends."""
    delta: dict = {}
    if content is not None:
        delta["content"] = content

    body: dict = {
        "id": "chatcmpl-stub-0000000000",
        "object": "chat.completion.chunk",
        "created": 1_760_000_000,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }

    if usage is not None:
        # A usage chunk carries no choices. Providers only send it when the
        # request asked for stream_options.include_usage, which is why the
        # gateway adds that flag -- without it every streamed request would
        # be recorded as costing nothing.
        body["choices"] = []
        body["usage"] = usage

    return f"data: {json.dumps(body)}\n\n"


def completion_stream(reply: str = CANNED_REPLY, *, usage: dict | None = None) -> bytes:
    """A whole streamed reply: the words one at a time, then [DONE]."""
    events = [chunk(word + " ") for word in reply.split()]
    events.append(chunk(finish="stop"))
    if usage is not None:
        events.append(chunk(usage=usage))
    events.append("data: [DONE]\n\n")
    return "".join(events).encode()


class BrokenStream(httpx.AsyncByteStream):
    """A stream that stops partway through, the way a real one can.

    A provider's connection can drop mid-reply: the caller already has half
    an answer and there is no status code left to change. What the gateway
    does next -- whether the event is still recorded, whether the connection
    is closed -- cannot be reasoned about, only tested.
    """

    def __init__(self, chunks: list[bytes], error: Exception) -> None:
        self.chunks = chunks
        self.error = error
        self.closed = False

    async def __aiter__(self):
        for piece in self.chunks:
            yield piece
        raise self.error

    async def aclose(self) -> None:
        self.closed = True


def breaks_after(events: int, *, reply: str = CANNED_REPLY) -> BrokenStream:
    """A reply that sends `events` chunks and then the connection drops."""
    whole = completion_stream(reply).decode().split("\n\n")
    sent = [f"{line}\n\n".encode() for line in whole[:events] if line.strip()]
    return BrokenStream(sent, httpx.ReadError("the connection dropped"))
