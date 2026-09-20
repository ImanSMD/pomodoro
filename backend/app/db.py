"""Async engine, session factory and the request-scoped session dependency.

The engine is owned by the application lifespan rather than created at import
time, so tests can build an app against a different database without mutating
the environment. The lifespan also probes the connection once at startup —
create_async_engine itself is lazy, so without that probe a bad DATABASE_URL
would report a healthy app and fail only on the first data route.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from starlette.requests import HTTPConnection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(url: str) -> AsyncEngine:
    # pool_pre_ping because the database may be restarted underneath a running
    # api container (compose down/up of db alone), which otherwise hands out
    # dead connections until the pool recycles.
    return create_async_engine(url, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: attributes stay readable after commit, so a route
    # can return the object it just wrote without triggering lazy IO on a
    # closed session.
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_db(connection: HTTPConnection) -> AsyncIterator[AsyncSession]:
    # HTTPConnection, not Request: FastAPI binds Request and WebSocket to
    # separate dependant slots, so a Request-annotated dependency is called with
    # no arguments on a WebSocket route and raises TypeError. HTTPConnection is
    # the common base of both, and 1.5 adds WS /ws.
    session_factory: async_sessionmaker[AsyncSession] = connection.app.state.session_factory
    async with session_factory() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_db)]
