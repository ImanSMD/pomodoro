"""Shared test fixtures.

Minimal by design: 1.2 needs a migrated test database to verify db.py, and
section 1.7 extends this with per-test transaction rollback and an
authenticated client fixture.

Tests run against a real Postgres, never SQLite — citext, the partial unique
index in 1.5 and the AT TIME ZONE bucketing in phase 3 are all Postgres-only,
and would pass or fail differently anywhere else.
"""

from __future__ import annotations

import os
import subprocess
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.config import Settings, get_settings
from app.db import create_engine

BACKEND_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def settings() -> Settings:
    return get_settings()


@pytest.fixture(scope="session")
def test_database_url(settings: Settings) -> str:
    if not settings.test_database_url:
        # fail, never skip. This fixture used to skip from a session-scoped
        # autouse fixture, which skipped every test in the suite — including the
        # ones needing no database — and still exited 0. The exit gate reads
        # that as green with zero assertions run.
        pytest.fail(
            "TEST_DATABASE_URL is not set, so no database test can run. "
            "It is configured by compose; if the database is missing, recreate "
            "the volume with `docker compose down -v`."
        )
    return settings.test_database_url


@pytest.fixture(scope="session")
def migrated_test_db(test_database_url: str) -> str:
    """Bring the test database to head.

    Not autouse: tests that touch no database must not depend on one. Via
    subprocess rather than alembic's Python API, because env.py drives the async
    engine with asyncio.run(), which cannot run inside pytest-asyncio's loop.
    """
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        env={**os.environ, "ALEMBIC_USE_TEST_DB": "1"},
        capture_output=True,
    )
    if result.returncode != 0:
        # check=True would raise CalledProcessError, whose message is just the
        # exit status — the actual cause (bad revision, missing citext,
        # connection refused) would sit unread in the captured stderr.
        pytest.fail(
            "alembic upgrade head failed against the test database:\n"
            + result.stderr.decode(errors="replace")
        )
    return test_database_url


@pytest.fixture(scope="session")
async def engine(migrated_test_db: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(migrated_test_db)
    yield engine
    await engine.dispose()


@asynccontextmanager
async def rolled_back_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session inside an outer transaction that is always rolled back.

    Extracted from the `db` fixture so a test can exercise this exact code
    rather than a copy of it.

    The outer transaction is the load-bearing part. A plain session on the
    engine plus `rollback()` on teardown does nothing once the test has
    committed — and from 1.3 the tests drive real routes through get_db, which
    commit. Rows would then survive across tests and across pytest runs: a
    "register -> 201" test would pass once and 409 forever after, and a later
    "duplicate email -> 409" test would pass for the wrong reason.

    join_transaction_mode is stated explicitly for the reader; SQLAlchemy's
    default ("conditional_savepoint") already savepoints on a connection that
    has an open transaction, so it is documentation rather than the mechanism.
    """
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()


@pytest.fixture
async def db(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with rolled_back_session(engine) as session:
        yield session


@pytest.fixture
def test_settings(settings: Settings, test_database_url: str) -> Settings:
    """Settings pointed at the test database.

    Depends on the URL, not on migrations: test_health touches no table, and
    requiring migrations here would make it fail when Postgres is down — the
    same coupling the migrated_test_db fixture avoids by not being autouse.

    Rebuilt rather than model_copy(update=...): model_copy skips the
    mode="after" validator, so a copy that changed cors_origins would keep the
    old allowlist while the raw field read as the new value.
    """
    return Settings(**{**settings.model_dump(), "database_url": test_database_url})
