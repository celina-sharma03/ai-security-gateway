"""Who is calling.

A gateway key can arrive two ways, and the difference decides what happens to
the header afterwards.

**`Authorization: Bearer gw_live_...`** -- the ordinary case. The developer puts
the gateway key exactly where the provider key used to go, so an OpenAI client
needs no special handling at all:

    OpenAI(api_key="gw_live_...", base_url="http://localhost:8080/v1")

The gateway then **strips that header** before forwarding. It has to: sending
`gw_live_...` to OpenAI would hand a third party a working credential for your
gateway, written into their logs. Step 4 puts the real provider key in its
place.

**`X-Gateway-Key: gw_live_...`** -- for passthrough, where the caller is still
using their own provider key in `Authorization`. The gateway identifies them
without touching the credential it was not given.

A missing key and a wrong key get the same answer, and a revoked key gets that
answer too. The caller learns only that it was refused; which of the three it
was belongs in the gateway's own records.
"""

from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.auth import keys as keyring
from gateway.storage import database
from gateway.storage.models import ApiKey, now

HEADER = "X-Gateway-Key"

TOUCH_AFTER = timedelta(seconds=60)
"""How stale `last_used_at` is allowed to be before it is written again.

Updating it on every request would mean a database write per call, in the
request path, to answer a question nobody asks to the second. A minute's
resolution answers the real question -- "is this key still in use, or can I
revoke it?" -- for a fraction of the cost.
"""


class AuthError(Exception):
    """Refused. The message is for the caller, so it says what to do and
    nothing about what went wrong internally."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def presented(request: Request) -> tuple[str | None, bool]:
    """The gateway key, and whether it came from the Authorization header."""
    explicit = request.headers.get(HEADER)
    if explicit:
        return explicit.strip(), False

    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        # Only treat it as ours if it looks like ours. Otherwise this is a
        # provider key in passthrough mode, and taking it for a gateway key
        # would turn a working request into a confusing 401.
        if token.startswith(keyring.PREFIX):
            return token, True

    return None, False


async def touch(session: AsyncSession, key: ApiKey) -> None:
    moment = now()

    if key.last_used_at is None or moment - key.last_used_at > TOUCH_AFTER:
        key.last_used_at = moment
        await session.commit()


async def require_key(
    request: Request,
    session: Annotated[AsyncSession, Depends(database.session)],
) -> ApiKey:
    token, from_authorization = presented(request)

    if token is None:
        raise AuthError(
            "No gateway key. Send it as `Authorization: Bearer gw_live_...`, "
            f"or as `{HEADER}` if you are also sending a provider key."
        )

    key = await keyring.authenticate(session, token)

    if key is None:
        raise AuthError("That gateway key is not valid.")

    # The route reads this to decide whether to strip the header on the way
    # out. Keeping it here means the route never has to know how a key is
    # presented, only that one was.
    request.state.strip_authorization = from_authorization

    await touch(session, key)
    return key
