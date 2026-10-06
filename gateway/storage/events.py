"""Recording what happened to a request.

One row per request that reached a verdict, plus one row per category found.
No content, ever -- see the comment at the top of models.py for why that is
the whole point rather than an omission.

**A failure here must never fail the request.** By the time this is called the
work is done: the checks ran, the provider answered, and the caller is about
to get their reply. Turning that into a 500 because a log line could not be
written would mean an outage of the gateway every time the database hiccups,
for no gain. So `record_safely` swallows the failure, rolls the session back
so the next writer finds it clean, and leaves a note in the log saying what
*kind* of error it was.

That is a deliberate trade with a real cost: under a database problem the
gateway keeps working and the audit trail quietly gains holes. The alternative
-- refusing traffic to protect the completeness of a log -- is worse, and the
warning in the log is what makes the holes noticeable.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from gateway.storage.models import ApiKey, Event, EventCategory

log = logging.getLogger("gateway.events")


async def record(
    session: AsyncSession,
    *,
    key: ApiKey,
    model: str | None,
    mode: str,
    decided: str,
    action: str,
    categories: list[str],
    upstream_status: int | None = None,
    latency_ms: int | None = None,
    error: str | None = None,
) -> Event:
    """Write one event. Raises if it cannot -- see `record_safely`."""
    event = Event(
        tenant_id=key.tenant_id,
        api_key_id=key.id,
        model=model,
        mode=mode,
        decided=decided,
        action=action,
        blocked=action == "block",
        upstream_status=upstream_status,
        latency_ms=latency_ms,
        error=error,
    )
    event.categories = [EventCategory(category=category) for category in categories]

    session.add(event)
    await session.commit()
    return event


async def record_safely(session: AsyncSession, **fields) -> None:
    """The same, but a failure is a log line rather than an outage."""
    try:
        await record(session, **fields)
    except Exception as exc:
        # The type only, never the message. A database error can quote the
        # row it was writing, and although this row holds no content, the
        # habit is worth keeping in the one place it would be easy to break.
        await session.rollback()
        log.warning("could not record an event: %s", type(exc).__name__)
