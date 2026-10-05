"""What the proxy needs to know about a provider, and nothing more.

Every provider has its own request shape. OpenAI puts the conversation in
`messages`, each with a role and a content; Anthropic keeps the system prompt
in a field of its own; others differ again. The proxy must not care, or adding
a provider would mean editing the proxy.

So each provider gets an adapter, and the proxy only ever asks it three things:

    what text is in this request, in order?
    here is that text, cleaned -- give me the request back.
    say "blocked" in a shape this provider's clients can read.

The third one exists because a blocked request must not look like a crash. A
caller does `response.choices[0].message.content`; if the gateway answers with
an HTTP error instead, their application raises an exception and their user
sees a spinner. Answering in the provider's own shape means the person reads
"this was blocked because it contained a card number" and understands.
"""

from typing import Protocol


class Provider(Protocol):
    """The whole contract. Anything satisfying this can be proxied."""

    name: str

    def texts(self, body: dict) -> list[str]:
        """Every piece of text in the request that would leave the network.

        Order matters, and must be stable: `with_texts` relies on getting the
        same list back in the same order.
        """
        ...

    def with_texts(self, body: dict, texts: list[str]) -> dict:
        """A copy of the request with those texts put back where they came from.

        A copy, never the original -- the caller's body is theirs, and a proxy
        that mutates what it was handed is a proxy that is impossible to debug.
        """
        ...

    def blocked_response(self, body: dict, message: str) -> dict:
        """A normal-shaped response that says the request was blocked.

        The message is written by the caller, not here: what to say is product
        copy, while the shape it goes in is the provider's business.
        """
        ...
