"""Phase 5: issuing and recognising gateway keys.

The test that matters most is the plain one: after issuing a key, the key
itself is nowhere in the database. Everything else is behaviour; that one is
the promise.
"""

from sqlalchemy import select

from gateway.auth import keys as keyring
from gateway.storage.models import ApiKey, Tenant

# --- making them --------------------------------------------------------


def test_a_key_announces_what_it_is():
    """`gw_live_` so a secret scanner, a reviewer, or a developer pasting into
    the wrong window all recognise it without knowing this project."""
    key = keyring.generate()

    assert key.startswith("gw_live_")
    assert len(key) > 40


def test_two_keys_are_never_the_same():
    assert len({keyring.generate() for _ in range(1000)}) == 1000


def test_the_fingerprint_is_stable_and_not_the_key():
    key = keyring.generate()

    assert keyring.fingerprint(key) == keyring.fingerprint(key)
    assert key not in keyring.fingerprint(key)
    assert len(keyring.fingerprint(key)) == 64


# --- storing them -------------------------------------------------------


async def test_the_key_itself_is_never_stored(db):
    """The promise of the whole design: a stolen database contains no working
    keys, because the gateway kept only a hash of each one."""
    key, _ = await keyring.issue(db, "Billing", "CI")

    rows = (await db.execute(select(ApiKey))).scalars().all()
    stored = " ".join(f"{row.prefix} {row.hash} {row.label}" for row in rows)

    assert key not in stored
    assert keyring.fingerprint(key) in stored


async def test_only_the_visible_part_is_kept_in_clear(db):
    key, row = await keyring.issue(db, "Billing", "CI")

    assert row.prefix == key[:16]
    assert len(row.prefix) < len(key) / 2


async def test_issuing_creates_the_tenant_the_first_time(db):
    await keyring.issue(db, "Billing", "CI")
    await keyring.issue(db, "Billing", "laptop")
    await keyring.issue(db, "Support", "CI")

    names = sorted((await db.execute(select(Tenant.name))).scalars())

    assert names == ["Billing", "Support"]


# --- using them ---------------------------------------------------------


async def test_a_real_key_is_recognised(db):
    key, row = await keyring.issue(db, "Billing", "CI")

    found = await keyring.authenticate(db, key)

    assert found is not None
    assert found.id == row.id
    assert found.tenant.name == "Billing"


async def test_an_unknown_key_is_not(db):
    await keyring.issue(db, "Billing", "CI")

    assert await keyring.authenticate(db, keyring.generate()) is None


async def test_something_that_is_not_a_key_at_all_is_rejected_without_a_lookup(db):
    assert await keyring.authenticate(db, "") is None
    assert await keyring.authenticate(db, "Bearer sk-not-a-real-key-0000") is None
    assert await keyring.authenticate(db, "hunter2") is None


# --- revoking them ------------------------------------------------------


async def test_a_revoked_key_stops_working(db):
    key, _ = await keyring.issue(db, "Billing", "old laptop")
    assert await keyring.authenticate(db, key) is not None

    await keyring.revoke(db, keyring.visible_part(key))

    assert await keyring.authenticate(db, key) is None


async def test_revoking_something_that_is_not_there_says_so(db):
    """Returns None rather than pretending. A command that reports success for
    a key it never found is how someone walks away believing they have cut off
    access they have not."""
    assert await keyring.revoke(db, "gw_live_nothing") is None


async def test_a_revoked_key_is_still_listed(db):
    """It still explains the events that arrived on it."""
    key, _ = await keyring.issue(db, "Billing", "old laptop")
    await keyring.revoke(db, keyring.visible_part(key))

    listed = await keyring.all_keys(db)

    assert len(listed) == 1
    assert not listed[0].active


async def test_revoking_twice_is_not_a_second_success(db):
    key, _ = await keyring.issue(db, "Billing", "CI")
    await keyring.revoke(db, keyring.visible_part(key))

    assert await keyring.revoke(db, keyring.visible_part(key)) is None
