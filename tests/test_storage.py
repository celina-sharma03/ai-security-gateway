"""Phase 5: what the gateway records.

Every test here runs against its own database, created and thrown away in
memory. Nothing touches gateway.db, so a test can never corrupt real data and
the suite still finishes in a second.

The last test is the phase's exit criterion in miniature: two tenants' traffic,
told apart by a query.
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from gateway.storage.models import ApiKey, Event, EventCategory, Tenant, now

# The `db` fixture lives in conftest.py, shared with the other storage tests.


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
        (
            await db.execute(
                select(Tenant.name, func.count(Event.id)).join(Event).group_by(Tenant.name)
            )
        ).all()
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


def test_the_pool_checks_a_connection_before_handing_it_out():
    """Found with streaming, against a real provider.

    When a caller closes the tab partway through a streamed reply, the
    request task is cancelled while its database connection is still checked
    out, and a broken one goes back to the pool. The *next* request then
    fails before its own route even runs, on the query that looks up the API
    key:

        sqlite3.OperationalError: no active connection
        [SQL: SELECT ... FROM api_keys WHERE api_keys.hash = ?]

    One person closing a browser tab made the next person's request fail
    with a 500. Reproduced three times, fixed by pre-ping, and verified by
    three more rounds of hanging up mid-stream.

    Asserted on the engine rather than by cancelling a real request, because
    the test client reads a response whole and cannot hang up halfway. A
    weaker test than the bug deserves, and better than trusting a one-line
    setting nobody can see.
    """
    from gateway.storage.database import engine

    assert engine().pool._pre_ping, (
        "without pre-ping, a client hanging up mid-stream breaks the next request"
    )


# --- Phase 9: what each key and model used ------------------------------
# Tokens, never money. The gateway records what the provider reported and
# leaves the arithmetic to whoever knows their own rates -- a price list we
# do not maintain is a number that eventually disagrees with an invoice.


async def a_priced_event(
    db, key: ApiKey, *, prompt: int | None, completion: int | None, model: str = "m1"
):
    total = None if prompt is None or completion is None else prompt + completion
    db.add(
        Event(
            tenant_id=key.tenant_id,
            api_key_id=key.id,
            model=model,
            mode="enforce",
            decided="allow",
            action="allow",
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
        )
    )
    await db.commit()


async def a_key(db, tenant: Tenant, prefix: str = "gw_live_aaa1") -> ApiKey:
    key = ApiKey(tenant_id=tenant.id, label="CI", prefix=prefix, hash=prefix * 8)
    db.add(key)
    await db.commit()
    return key


async def test_tokens_are_counted_per_key(db):
    from gateway.storage import queries

    tenant = await a_tenant(db)
    key = await a_key(db, tenant)
    await a_priced_event(db, key, prompt=10, completion=20)
    await a_priced_event(db, key, prompt=5, completion=7)

    (row,) = await queries.spend_by_key(db)
    _, _, _, requests, prompt, completion, unknown = row

    assert (requests, prompt, completion, unknown) == (2, 15, 27, 0)


async def test_prompt_and_completion_are_kept_apart(db):
    """Providers charge differently for the two, often four to one. Added
    together they could never be priced afterwards, and the split cannot be
    recovered once it is lost."""
    from gateway.storage import queries

    tenant = await a_tenant(db)
    key = await a_key(db, tenant)
    await a_priced_event(db, key, prompt=100, completion=400)

    (row,) = await queries.spend_by_key(db)

    assert row[4] == 100
    assert row[5] == 400


async def test_a_request_with_no_reported_cost_is_counted_as_unknown(db):
    """Not as zero. A request that never reached the provider did not cost
    nothing -- nobody ever said what it cost. Six failed requests looking
    like free traffic is the Phase 5 mistake, and this is where it would
    come back."""
    from gateway.storage import queries

    tenant = await a_tenant(db)
    key = await a_key(db, tenant)
    await a_priced_event(db, key, prompt=10, completion=20)
    await a_priced_event(db, key, prompt=None, completion=None)

    (row,) = await queries.spend_by_key(db)
    _, _, _, requests, prompt, completion, unknown = row

    assert requests == 2
    assert unknown == 1, "a request of unknown cost was counted as free"
    assert (prompt, completion) == (10, 20)


async def test_a_key_with_no_traffic_has_nothing_unknown(db):
    """The outer join invents a row for a key that has never been used.
    Counting it made an unused key report one request of unknown cost --
    a number about nothing, in a report whose only value is being trusted."""
    from gateway.storage import queries

    tenant = await a_tenant(db)
    await a_key(db, tenant)

    (row,) = await queries.spend_by_key(db)
    _, _, _, requests, prompt, completion, unknown = row

    assert (requests, prompt, completion, unknown) == (0, 0, 0, 0)


async def test_tokens_are_counted_per_model(db):
    """Per model because that is the grain a price list works at. Without
    the model name these numbers could never be priced at all."""
    from gateway.storage import queries

    tenant = await a_tenant(db)
    key = await a_key(db, tenant)
    await a_priced_event(db, key, prompt=10, completion=20, model="fast")
    await a_priced_event(db, key, prompt=30, completion=40, model="slow")
    await a_priced_event(db, key, prompt=1, completion=2, model="fast")

    rows = {row[0]: row[1:] for row in await queries.spend_by_model(db)}

    assert rows["fast"] == (2, 11, 22, 0)
    assert rows["slow"] == (1, 30, 40, 0)
