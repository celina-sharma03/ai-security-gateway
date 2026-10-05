"""The outgoing side: handing an allowed request to the real provider.

All the networking lives here, so the route above it stays about decisions.
It is also what makes the proxy testable: a test hands this class a fake
transport and the route never knows the difference.

Three rules live here.

**The caller's key stays the caller's.** The gateway forwards the Authorization
header it was given and holds no key of its own in V1. Nothing here reads it,
stores it or logs it. Per-key auth arrives in Phase 5.

**Headers are copied by allowlist, never wholesale.** `Host` would name the
gateway rather than the provider, `Content-Length` is wrong the moment text is
redacted, and cookies have no business leaving the network. Copying everything
is how proxies leak.

**A provider that is down is not the same as a check that crashed.** A broken
check fails open and the request carries on, because the request is still
valid. A provider that cannot be reached leaves nothing to carry on with --
there is no answer to invent -- so the failure is raised and the caller is
told the truth.
"""

from dataclasses import dataclass

import httpx

from gateway.settings import settings

FORWARDED_HEADERS = frozenset(
    {
        "authorization",
        "content-type",
        "accept",
        # Provider-specific headers that carry real meaning. Unknown ones are
        # dropped rather than guessed at.
        "openai-organization",
        "openai-project",
        "openai-beta",
        "x-api-key",
        "anthropic-version",
    }
)


class UpstreamError(Exception):
    """The provider could not be reached, or did not answer in time.

    Carries the type of failure and never the provider's message, for the same
    reason the pipeline records only error types: a message can quote the
    request, and the request is what we are protecting.
    """


@dataclass(frozen=True)
class UpstreamResponse:
    """What came back, in the three parts the route needs to answer with."""

    status_code: int
    body: bytes
    media_type: str


def forwarded_headers(headers: dict[str, str]) -> dict[str, str]:
    """Only the headers on the allowlist, with their original casing dropped."""
    return {name: value for name, value in headers.items() if name.lower() in FORWARDED_HEADERS}


class Upstream:
    """One HTTP client, reused for every forwarded request.

    Reused on purpose: a new client per request means a new TCP connection and
    a new TLS handshake every time, which is slower than the check it is
    waiting behind.
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or settings.upstream_base_url).rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=timeout if timeout is not None else settings.upstream_timeout,
            transport=transport,
        )

    async def send(self, path: str, body: dict, headers: dict[str, str]) -> UpstreamResponse:
        """Forward one request and bring back what the provider said.

        The provider's own errors -- a 429, a 500 -- are not failures here.
        They are the provider's answer, and they are passed through untouched,
        because a caller being rate limited needs to know that and not a
        gateway error invented on top of it.
        """
        url = f"{self.base_url}/{path.lstrip('/')}"

        try:
            response = await self._client.post(
                url, json=body, headers=forwarded_headers(headers)
            )
        except httpx.TimeoutException as exc:
            raise UpstreamError(
                f"{type(exc).__name__}: the provider did not answer in time"
            ) from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(
                f"{type(exc).__name__}: the provider could not be reached"
            ) from exc

        return UpstreamResponse(
            status_code=response.status_code,
            body=response.content,
            media_type=response.headers.get("content-type", "application/json"),
        )

    async def close(self) -> None:
        """Closed on shutdown, so connections aren't left open."""
        await self._client.aclose()
