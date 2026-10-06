"""Fixtures shared across the test suite.

Two kinds of database here, for two different jobs.

`db` is an in-memory session handed straight to code under test -- fast, and
enough for anything that takes a session as an argument.

`file_database` is a real file that the *application* opens for itself, which
is what the proxy does: it builds its own engine from settings, inside its own
event loop. A session created out here would belong to a different loop and
SQLAlchemy would object, so the fixture points the settings at a throwaway
file and lets the app do the rest.
"""

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from gateway.auth import keys as keyring
from gateway.settings import settings
from gateway.storage import database
from gateway.storage.models import Base


@pytest_asyncio.fixture
async def db():
    """A fresh in-memory database per test.

    StaticPool because every new connection to an in-memory SQLite gets its
    *own* empty database. Without it the tables would be created on one
    connection and queried on another -- a confusing failure that looks like
    the data vanished between two lines.

    Nothing here touches gateway.db, so no test can corrupt real data.
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


@pytest.fixture
def file_database(tmp_path, monkeypatch):
    """Point the gateway's own settings at a throwaway database file.

    `database.reset()` on both sides because the engine is cached per process:
    without it, the second test to run would reuse the first one's file.
    """
    url = f"sqlite+aiosqlite:///{tmp_path / 'gateway-test.db'}"
    monkeypatch.setattr(settings, "database_url", url)

    async def build() -> None:
        await database.reset()
        engine = create_async_engine(url)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(build())
    yield url
    asyncio.run(database.reset())


@pytest.fixture
def gateway_key(file_database) -> str:
    """A real issued key, in the database the app will open."""

    async def issue() -> str:
        engine = create_async_engine(file_database)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            key, _ = await keyring.issue(session, "Billing", "tests")
        await engine.dispose()
        return key

    return asyncio.run(issue())
