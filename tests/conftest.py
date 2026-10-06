"""Fixtures shared across the test suite."""

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

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
