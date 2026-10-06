"""Reading the events back.

Phase 5 exists for the questions in this file. Everything before it put rows
in; this is what makes them worth having.

The queries are plain SQL through SQLAlchemy -- no dialect tricks, nothing
SQLite can do that Postgres cannot or the reverse -- because the promise that
`GATEWAY_DATABASE_URL` is the only thing that changes between them is only
kept by writing code that keeps it.
"""

from sqlalchemy import case, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from gateway.storage.models import ApiKey, Event, EventCategory, Tenant


async def recent(session: AsyncSession, limit: int = 20, tenant: str | None = None) -> list[Event]:
    """The last few requests, newest first.

    `selectinload` rather than letting the relationships load themselves: a
    lazy load inside an async session raises, and even where it works, printing
    twenty rows would quietly run sixty extra queries.
    """
    query = (
        select(Event)
        .options(
            selectinload(Event.tenant), selectinload(Event.api_key), selectinload(Event.categories)
        )
        .order_by(desc(Event.at))
        .limit(limit)
    )

    if tenant:
        query = query.join(Tenant).where(Tenant.name == tenant)

    return list((await session.execute(query)).scalars())


async def totals(session: AsyncSession) -> list[tuple[str, int, int, int, int]]:
    """Per tenant: requests, blocked, redacted, tokens.

    This is the phase's exit criterion as a single query -- two tenants'
    traffic, told apart, with tenant labels on every row.
    """
    query = (
        select(
            Tenant.name,
            func.count(Event.id),
            func.sum(case((Event.blocked, 1), else_=0)),
            func.sum(case((Event.action == "redact", 1), else_=0)),
            func.coalesce(func.sum(Event.total_tokens), 0),
        )
        .join(Event, Event.tenant_id == Tenant.id)
        .group_by(Tenant.name)
        .order_by(Tenant.name)
    )

    return [tuple(row) for row in (await session.execute(query)).all()]


async def caught(session: AsyncSession, tenant: str | None = None) -> list[tuple[str, int]]:
    """How often each category was found. "Fourteen card numbers this week",
    answered without a single card number being stored."""
    query = (
        select(EventCategory.category, func.count(EventCategory.id))
        .join(Event, Event.id == EventCategory.event_id)
        .group_by(EventCategory.category)
        .order_by(desc(func.count(EventCategory.id)))
    )

    if tenant:
        query = query.join(Tenant, Tenant.id == Event.tenant_id).where(Tenant.name == tenant)

    return [tuple(row) for row in (await session.execute(query)).all()]


async def key_usage(session: AsyncSession) -> list[tuple[str, str, int]]:
    """Requests per key, so a key nobody uses can be found and revoked."""
    query = (
        select(Tenant.name, ApiKey.prefix, func.count(Event.id))
        .join(ApiKey, ApiKey.tenant_id == Tenant.id)
        .outerjoin(Event, Event.api_key_id == ApiKey.id)
        .group_by(Tenant.name, ApiKey.prefix)
        .order_by(desc(func.count(Event.id)))
    )

    return [tuple(row) for row in (await session.execute(query)).all()]
