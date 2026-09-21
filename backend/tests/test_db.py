"""Database foundation: migrations, session wiring and the schema they produce."""

from __future__ import annotations

import asyncpg
import httpx
import pytest
from fastapi import WebSocket
from starlette.testclient import TestClient
from asgi_lifespan import LifespanManager
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.config import Settings
from app.db import DbSession
from sqlalchemy.exc import DBAPIError
from app.main import create_app
from app.models import User
from tests.conftest import rolled_back_session


async def test_session_executes_against_the_test_database(db: AsyncSession):
    assert await db.scalar(select(text("1"))) == 1


async def test_migrations_created_the_citext_extension(db: AsyncSession):
    # The users table depends on it, and autogenerate does not emit it — so
    # this asserts the hand-written part of migration 0001.
    assert await db.scalar(
        text("select exists (select 1 from pg_extension where extname = 'citext')")
    )


async def test_email_uniqueness_ignores_case(db: AsyncSession):
    db.add(User(email="Ada@Example.com", password_hash="x", display_name="Ada"))
    await db.flush()

    db.add(User(email="ada@example.COM", password_hash="y", display_name="Dup"))
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_lookup_by_email_ignores_case(db: AsyncSession):
    db.add(User(email="Grace@Example.com", password_hash="x", display_name="Grace"))
    await db.flush()

    found = await db.scalar(select(User).where(User.email == "GRACE@EXAMPLE.COM"))
    assert found is not None and found.display_name == "Grace"


async def test_server_defaults_apply_to_rows_created_without_them(db: AsyncSession):
    user = User(email="defaults@example.com", password_hash="x", display_name="D")
    db.add(user)
    await db.flush()
    await db.refresh(user)

    assert user.timezone == "Asia/Tehran"
    assert user.calendar_pref == "jalali"
    assert (user.default_work_minutes, user.default_break_minutes) == (25, 5)
    assert (user.long_break_minutes, user.rounds_before_long_break) == (15, 4)
    assert (user.auto_start_breaks, user.sound_enabled) == (False, True)
    assert user.notifications_enabled is True


async def test_timestamps_are_timezone_aware(db: AsyncSession):
    user = User(email="tz@example.com", password_hash="x", display_name="T")
    db.add(user)
    await db.flush()
    await db.refresh(user)

    # Phase 3 buckets sessions by the user's local day; that is impossible from
    # naive timestamps, so this must hold for every table.
    assert user.created_at.tzinfo is not None
    assert user.updated_at.tzinfo is not None


async def test_get_db_yields_a_working_session_through_the_app(test_settings: Settings):
    app = create_app(test_settings)

    # A probe route, because get_db is the only new runtime path in db.py and
    # asserting on app.state alone would leave it uncovered: a typo in
    # request.app.state.session_factory, or a get_db that never yields, would
    # ship green under a test named for it.
    @app.get("/probe")
    async def probe(db: DbSession) -> dict[str, int]:
        return {"one": await db.scalar(select(text("1")))}

    async with LifespanManager(app) as manager:
        # State is read off the FastAPI instance, not manager.app — the manager
        # exposes the wrapped ASGI callable, which has no .state.
        assert app.state.session_factory is not None
        assert app.state.engine is not None

        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/api/health")).status_code == 200

            response = await client.get("/probe")
            assert response.status_code == 200, response.text
            assert response.json() == {"one": 1}


def test_get_db_works_on_a_websocket_route(test_settings: Settings):
    """A Request-annotated dependency is never bound on a WebSocket route.

    1.5 adds WS /ws, which needs a session for get_current_user and for session
    writes, so this would have failed two sections from now with
    "get_db() missing 1 required positional argument".

    Sync, because TestClient drives its own event loop and lifespan; nesting it
    inside an async test deadlocks.
    """
    app = create_app(test_settings)

    @app.websocket("/ws-probe")
    async def ws_probe(websocket: WebSocket, db: DbSession) -> None:
        await websocket.accept()
        await websocket.send_json({"one": await db.scalar(select(text("1")))})
        await websocket.close()

    with TestClient(app) as client:
        with client.websocket_connect("/ws-probe") as ws:
            assert ws.receive_json() == {"one": 1}


async def test_startup_fails_loudly_on_an_unreachable_database(test_settings: Settings):
    # The engine is lazy, so without the lifespan probe this app would report a
    # healthy startup and only fail on the first data route.
    broken = Settings(
        **{
            **test_settings.model_dump(),
            "database_url": "postgresql+asyncpg://nobody:nobody@db:5432/does_not_exist",
        }
    )
    # asyncpg's connection-time errors are not always wrapped by SQLAlchemy,
    # so accept either shape — the point is that startup raises at all.
    with pytest.raises((DBAPIError, asyncpg.PostgresError)):
        async with LifespanManager(create_app(broken)):
            pass


async def test_id_is_generated_by_the_database_for_non_orm_inserts(db: AsyncSession):
    # A data migration or seed script inserting without an id must not fail on
    # NOT NULL — the ORM-side uuid4 default does not help those paths.
    generated = await db.scalar(
        text(
            "INSERT INTO users (email, password_hash, display_name) "
            "VALUES ('raw@example.com', 'x', 'Raw') RETURNING id"
        )
    )
    assert generated is not None


async def test_absurdly_long_email_is_rejected_by_the_database(db: AsyncSession):
    # CITEXT is unbounded and the unique index is a btree, so without the check
    # constraint this fails deep in Postgres as a 500 rather than a clean error.
    long_email = "a" * 320 + "@example.com"
    db.add(User(email=long_email, password_hash="x", display_name="Long"))
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_the_db_fixture_rolls_back_even_after_a_commit(engine: AsyncEngine):
    """Exercises the db fixture's own helper, not a copy of it.

    Self-contained rather than a pair of ordered tests: a pair passes trivially
    under -k, xdist or any shuffling plugin, which is to say it would report
    green precisely when the isolation had regressed.
    """
    email = "committed@example.com"

    async with rolled_back_session(engine) as session:
        session.add(User(email=email, password_hash="x", display_name="C"))
        await session.commit()
        assert await session.scalar(select(User).where(User.email == email))

    # A separate connection: the committed row must not have survived teardown.
    async with engine.connect() as connection:
        assert await connection.scalar(select(User.id).where(User.email == email)) is None
