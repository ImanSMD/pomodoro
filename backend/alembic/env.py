"""Alembic environment, driven by the async engine.

The URL comes from Settings rather than alembic.ini so migrations cannot drift
onto a different database than the app uses.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    # -x db_url=... lets the test harness point migrations at the test database
    # without mutating the environment of a running container.
    override = context.get_x_argument(as_dictionary=True).get("db_url")
    if override:
        return override
    # Value-checked, not presence-checked: ALEMBIC_USE_TEST_DB=0 meant to
    # select the dev database would otherwise silently migrate the test one.
    if os.getenv("ALEMBIC_USE_TEST_DB", "").strip().lower() in {"1", "true", "yes", "on"}:
        settings = get_settings()
        if not settings.test_database_url:
            raise RuntimeError("ALEMBIC_USE_TEST_DB set but TEST_DATABASE_URL is empty")
        return settings.test_database_url
    return get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Without these, autogenerate silently ignores column type changes and
        # server-default changes — the two things most likely to drift.
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    # NullPool: migrations are a one-shot process, and a pooled connection
    # left open can block DDL that needs an exclusive lock. poolclass=None
    # is NOT this — it silently means 'use the default pool'.
    engine = create_async_engine(_database_url(), poolclass=NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
