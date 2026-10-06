"""Phase 5: what the gateway records.

Every test here runs against its own database, created and thrown away in
memory. Nothing touches gateway.db, so a test can never corrupt real data and
the suite still finishes in a second.

The last test is the phase's exit criterion in miniature: two tenants' traffic,
told apart by a query.
"""

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from gateway.storage.models import ApiKey, Base, Event, EventCategory, Tenant, now


@pytest_asyncio.fixture
async def db():
    """A fresh in-memory database per test.

    StaticPool because every connection to `sqlite://` in memory gets its *own*
    empty database. Without it the tables would be created on one connection
    and the test would query a different, empty one -- a confusing failure that
    looks like the data vanished.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session

    await engine.dispose()


async def a_tenant(db, name: str = "Billing") -> Tenant:
    tenant = Tenant(name=name)
    db.add(tenant)
    await db.commit()
    return tenant


# --- tenants and keys ---------------------------------------------------


async def test_a_key_belongs_to_a_tenant(db):
    tenant = await a_tenant(db)
    db.add(ApiKey(tenant_id=tenant.id, label="CI", prefix="gw_live_abc1", hash="a" * 64))
    await db.commit()

    key = (await db.execute(select(ApiKey))).scalar_one()

    assert key.tenant_id == tenant.id
    assert key.active


async def test_the_same_key_cannot_be_stored_twice(db):
    """The hash is unique, so an issuing bug that produced a duplicate key
    fails loudly at the database rather than quietly handing two tenants the
    same credential."""
    tenant = await a_tenant(db)
    db.add(ApiKey(tenant_id=tenant.id, label="one", prefix="gw_live_abc1", hash="a" * 64))
    await db.commit()

    db.add(ApiKey(tenant_id=tenant.id, label="two", prefix="gw_live_abc1", hash="a" * 64))

    with pytest.raises(IntegrityError):
        await db.commit()


async def test_a_revoked_key_is_not_active(db):
    tenant = await a_tenant(db)
    key = ApiKey(tenant_id=tenant.id, label="old laptop", prefix="gw_live_dead", hash="b" * 64)
    db.add(key)
    await db.commit()

    assert key.active

    key.revoked_at = now()
    await db.commit()

    assert not key.active


async def test_revoking_keeps_the_row(db):
    """Revoked, never deleted. "Which key did this come from" is exactly the
    question asked after something goes wrong, and a deleted key leaves the
    events pointing at nothing."""
    tenant = await a_tenant(db)
    key = ApiKey(tenant_id=tenant.id, label="gone", prefix="gw_live_gone", hash="c" * 64)
    db.add(key)
    await db.commit()

    key.revoked_at = now()
    await db.commit()

    assert (await db.execute(select(func.count()).select_from(ApiKey))).scalar_one() == 1


# --- events -------------------------------------------------------------


async def an_event(db, tenant: Tenant, *, action: str = "redact", categories=("credit_card",)):
    event = Event(
        tenant_id=tenant.id,
        model="openai/gpt-oss-20b",
        mode="enforce",
        decided=action,
        action=action,
        blocked=action == "block",
        prompt_tokens=40,
        completion_tokens=20,
        total_tokens=60,
        upstream_status=200,
        latency_ms=312,
    )
    event.categories = [EventCategory(category=category) for category in categories]
    db.add(event)
    await db.commit()
    return event


async def test_an_event_records_the_verdict(db):
    tenant = await a_tenant(db)
    await an_event(db, tenant)

    event = (await db.execute(select(Event))).scalar_one()

    assert event.action == "redact"
    assert event.total_tokens == 60
    assert [c.category for c in event.categories] == ["credit_card"]


async def test_an_event_has_nowhere_to_put_the_prompt(db):
    """The point of the schema, asserted rather than assumed. If someone ever
    adds a text column for content, this test says so."""
    columns = set(Event.__table__.columns.keys())

    assert not columns & {"prompt", "text", "content", "reply", "response", "mapping"}


async def test_deleting_an_event_takes_its_categories_with_it(db):
    tenant = await a_tenant(db)
    event = await an_event(db, tenant, categories=("credit_card", "email"))

    await db.delete(event)
    await db.commit()

    left = (await db.execute(select(func.count()).select_from(EventCategory))).scalar_one()
    assert left == 0


# --- the exit criterion, in miniature -----------------------------------


async def test_two_tenants_traffic_can_be_told_apart(db):
    """Phase 5 exists for this query.

    Two teams, different traffic, one GROUP BY, and the answer to "who is
    pasting card numbers" without a single card number being stored.
    """
    billing = await a_tenant(db, "Billing")
    support = await a_tenant(db, "Support")

    await an_event(db, billing, categories=("credit_card",))
    await an_event(db, billing, categories=("credit_card", "email"))
    await an_event(db, support, action="allow", categories=())

    counts = dict(
        (await db.execute(
            select(Tenant.name, func.count(Event.id)).join(Event).group_by(Tenant.name)
        )).all()
    )

    assert counts == {"Billing": 2, "Support": 1}

    cards = (
        await db.execute(
            select(func.count())
            .select_from(EventCategory)
            .join(Event)
            .join(Tenant)
            .where(Tenant.name == "Billing", EventCategory.category == "credit_card")
        )
    ).scalar_one()

    assert cards == 2
