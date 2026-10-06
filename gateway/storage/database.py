"""The connection to the database, and nothing else.

One engine for the life of the process, and a session per unit of work. The
engine holds the connection pool; a session is a short-lived workspace that
borrows a connection and gives it back. Sharing one session across requests is
the classic way to make a web application mysteriously slow and occasionally
wrong.

**Async, because the route is.** The proxy's handler is `async def` and spends
most of its time waiting on the provider. A blocking database call inside it
would stop the event loop -- not just that request, but every other request in
flight. So: `sqlite+aiosqlite` by default, `postgresql+asyncpg` when someone
needs more than one gateway instance.

The URL is the only thing that changes between those two. Which is true only
while the code stays portable: no `JSONB`, no `ON CONFLICT` tricks, nothing
one database can do and the other cannot.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from gateway.settings import settings

_engine: AsyncEngine | None = None
_sessions: async_sessionmaker[AsyncSession] | None = None


def engine() -> AsyncEngine:
    """The one engine, made on first use.

    Not made at import: a test needs to point this at its own database before
    anything touches the real one, and an import-time engine would have opened
    a file by then.
    """
    global _engine
    if _engine is None:
        _engine = create_async_engine(settings.database_url, future=True)
    return _engine


def sessions() -> async_sessionmaker[AsyncSession]:
    global _sessions
    if _sessions is None:
        _sessions = async_sessionmaker(engine(), expire_on_commit=False)
    return _sessions


async def session() -> AsyncIterator[AsyncSession]:
    """A session for one unit of work, closed afterwards whatever happens.

    `expire_on_commit=False` so objects stay readable after the commit. The
    default would reload them from the database on the next attribute access,
    which for a route that commits and then builds a response means a second
    round trip for data it already had.
    """
    async with sessions()() as current:
        yield current


async def reset() -> None:
    """Forget the engine, for tests that switch databases between them."""
    global _engine, _sessions

    if _engine is not None:
        await _engine.dispose()

    _engine = None
    _sessions = None
