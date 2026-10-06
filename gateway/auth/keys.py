"""Issuing the gateway's own API keys, and recognising them later.

A key looks like this:

    gw_live_K3n8Qx7fJ2mWp5RtYv9BzLcH4sNdA6gEuF1iO0jXyTk

`gw_live_` says what it is at a glance, which matters: a secret scanner, a code
reviewer or a developer pasting into the wrong window can all recognise it
without knowing anything about this project. The rest is 32 bytes from
`secrets.token_urlsafe` -- not `random`, which is predictable from a few
outputs and has no business anywhere near a credential.

**Only a hash is stored.** The key is shown once, when it is created, and the
gateway genuinely cannot show it again. That is the point rather than an
inconvenience: it means a stolen copy of the database contains no working
keys.

**SHA-256, not bcrypt.** Bcrypt is deliberately slow, which is right for human
passwords -- short, guessable, reused across sites -- where an attacker with
the hashes can try a dictionary. There is no dictionary for 32 random bytes;
an attacker with the hash has nothing to try. So the slowness would buy
nothing, and it would be paid on every single request, in the request path, by
every caller. The right tool for a high-entropy secret is a fast hash.
"""

import hashlib
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from gateway.storage.models import ApiKey, Tenant, now

PREFIX = "gw_live_"

VISIBLE = len(PREFIX) + 8
"""How much of a key is kept in clear, for recognising it in a list.

Eight characters of the random part is enough that two keys are never confused
and far too little to guess the remaining thirty-five.
"""


def generate() -> str:
    return PREFIX + secrets.token_urlsafe(32)


def fingerprint(key: str) -> str:
    """What gets stored. Not reversible, and the same key always gives the
    same answer -- which is how a presented key is found in one indexed
    lookup rather than by checking every row."""
    return hashlib.sha256(key.encode()).hexdigest()


def visible_part(key: str) -> str:
    return key[:VISIBLE]


async def tenant_named(session: AsyncSession, name: str) -> Tenant:
    """The tenant with this name, created if it is new.

    Deliberately not a separate "create tenant" step. Making a key for a team
    that does not exist yet is the normal case, and a command that fails with
    "no such tenant" before you have done anything is a worse first five
    minutes than one that just works.
    """
    found = (await session.execute(select(Tenant).where(Tenant.name == name))).scalar_one_or_none()

    if found is not None:
        return found

    tenant = Tenant(name=name)
    session.add(tenant)
    await session.flush()
    return tenant


async def issue(session: AsyncSession, tenant_name: str, label: str) -> tuple[str, ApiKey]:
    """Make a key. Returns the plaintext *once*, with the row that stores its
    hash. Nothing keeps the plaintext afterwards, including this function."""
    tenant = await tenant_named(session, tenant_name)

    key = generate()
    row = ApiKey(
        tenant_id=tenant.id,
        label=label,
        prefix=visible_part(key),
        hash=fingerprint(key),
    )

    session.add(row)
    await session.commit()

    return key, row


async def all_keys(session: AsyncSession) -> list[ApiKey]:
    """Every key, revoked ones included -- a revoked key still explains the
    events that arrived on it."""
    result = await session.execute(
        select(ApiKey).options(selectinload(ApiKey.tenant)).order_by(ApiKey.created_at)
    )
    return list(result.scalars())


async def revoke(session: AsyncSession, prefix: str) -> ApiKey | None:
    """Stop a key working, by the visible part -- which is all anyone has.

    Returns None if no live key matches, so the caller can say so rather than
    reporting a success that did not happen.
    """
    found = (
        await session.execute(
            select(ApiKey).where(ApiKey.prefix == prefix, ApiKey.revoked_at.is_(None))
        )
    ).scalar_one_or_none()

    if found is None:
        return None

    found.revoked_at = now()
    await session.commit()
    return found


async def authenticate(session: AsyncSession, presented: str) -> ApiKey | None:
    """The key this request arrived on, or None.

    One indexed lookup on the hash. A revoked key is treated exactly like an
    unknown one: the caller is told nothing it could learn from, and the
    difference is in the gateway's own records where it belongs.
    """
    if not presented.startswith(PREFIX):
        return None

    found = (
        await session.execute(
            select(ApiKey)
            .options(selectinload(ApiKey.tenant))
            .where(ApiKey.hash == fingerprint(presented))
        )
    ).scalar_one_or_none()

    if found is None or not found.active:
        return None

    return found
