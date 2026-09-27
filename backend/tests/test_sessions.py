"""Timer sessions: the one-running-session constraint, completion and fix-end."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models import Session
from app.schemas.session import SessionOut

pytestmark = pytest.mark.asyncio


async def _task(client: httpx.AsyncClient, title: str = "Write") -> str:
    response = await client.post("/api/tasks", json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _start(client: httpx.AsyncClient, task_id: str, **extra) -> httpx.Response:
    return await client.post("/api/sessions/start", json={"task_id": task_id, **extra})


async def test_requires_authentication(client: httpx.AsyncClient):
    assert (await client.get("/api/sessions/active")).status_code == 401


async def test_start_returns_a_running_session(authed_client: httpx.AsyncClient):
    task_id = await _task(authed_client)
    response = await _start(authed_client, task_id)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "running"
    assert body["kind"] == "work"
    assert body["ended_at"] is None


async def test_starting_a_second_session_is_409(authed_client: httpx.AsyncClient):
    """Enforced by the partial unique index, not by a pre-check.

    A read-then-write ("is anything running?" then INSERT) is interleaved by
    two rapid clicks; the index cannot be.
    """
    task_id = await _task(authed_client)
    assert (await _start(authed_client, task_id)).status_code == 201

    second = await _start(authed_client, task_id)
    assert second.status_code == 409, second.text


async def test_a_second_session_is_allowed_once_the_first_ends(
    authed_client: httpx.AsyncClient
):
    task_id = await _task(authed_client)
    first = (await _start(authed_client, task_id)).json()
    await authed_client.post(f"/api/sessions/{first['id']}/complete")

    assert (await _start(authed_client, task_id)).status_code == 201


async def test_two_users_may_each_run_one(
    authed_client: httpx.AsyncClient, second_user_token: str
):
    # The index is per user, so one person's timer must not block another's.
    mine = await _task(authed_client)
    assert (await _start(authed_client, mine)).status_code == 201

    headers = {"Authorization": f"Bearer {second_user_token}"}
    theirs = (
        await authed_client.post("/api/tasks", json={"title": "Theirs"}, headers=headers)
    ).json()["id"]
    response = await authed_client.post(
        "/api/sessions/start", json={"task_id": theirs}, headers=headers
    )
    assert response.status_code == 201, response.text


async def test_cannot_start_on_another_users_task(
    authed_client: httpx.AsyncClient, second_user_token: str
):
    task_id = await _task(authed_client)
    response = await authed_client.post(
        "/api/sessions/start",
        json={"task_id": task_id},
        headers={"Authorization": f"Bearer {second_user_token}"},
    )
    assert response.status_code == 404


@pytest.mark.parametrize(
    ("kind", "expected"),
    [("work", 25), ("short_break", 5), ("long_break", 15)],
)
async def test_planned_minutes_comes_from_the_matching_setting(
    authed_client: httpx.AsyncClient, kind: str, expected: int
):
    task_id = await _task(authed_client)
    body = (await _start(authed_client, task_id, kind=kind)).json()
    assert body["planned_minutes"] == expected


async def test_planned_minutes_is_frozen_at_start(authed_client: httpx.AsyncClient):
    """A later settings change must not rewrite a past session.

    Stored rather than derived for exactly this reason.
    """
    task_id = await _task(authed_client)
    started = (await _start(authed_client, task_id)).json()

    await authed_client.patch("/api/settings", json={"default_work_minutes": 50})

    active = (await authed_client.get("/api/sessions/active")).json()
    assert active["session"]["planned_minutes"] == started["planned_minutes"] == 25


async def test_active_returns_null_when_nothing_runs(authed_client: httpx.AsyncClient):
    body = (await authed_client.get("/api/sessions/active")).json()
    assert body["session"] is None
    assert body["server_now"] is not None


async def test_active_carries_the_server_clock(authed_client: httpx.AsyncClient):
    """The client corrects its countdown by this.

    A browser clock even a minute out would otherwise show the wrong
    remaining time, or a timer that never fires.
    """
    task_id = await _task(authed_client)
    await _start(authed_client, task_id)

    body = (await authed_client.get("/api/sessions/active")).json()
    server_now = datetime.fromisoformat(body["server_now"])
    assert server_now.tzinfo is not None
    assert abs((datetime.now(timezone.utc) - server_now).total_seconds()) < 60


async def test_complete_sets_duration_from_the_timestamps(
    authed_client: httpx.AsyncClient
):
    task_id = await _task(authed_client)
    started = (await _start(authed_client, task_id)).json()

    body = (await authed_client.post(f"/api/sessions/{started['id']}/complete")).json()
    assert body["status"] == "completed"
    assert body["ended_at"] is not None
    assert body["duration_seconds"] is not None and body["duration_seconds"] >= 0


async def test_completing_twice_does_not_move_the_end(authed_client: httpx.AsyncClient):
    # Phase 3 buckets by this timestamp.
    task_id = await _task(authed_client)
    started = (await _start(authed_client, task_id)).json()

    first = (await authed_client.post(f"/api/sessions/{started['id']}/complete")).json()
    second = (await authed_client.post(f"/api/sessions/{started['id']}/complete")).json()
    assert first["ended_at"] == second["ended_at"]
    assert first["duration_seconds"] == second["duration_seconds"]


async def test_cancel_records_the_time_but_marks_it_cancelled(
    authed_client: httpx.AsyncClient
):
    task_id = await _task(authed_client)
    started = (await _start(authed_client, task_id)).json()

    body = (await authed_client.post(f"/api/sessions/{started['id']}/cancel")).json()
    assert body["status"] == "cancelled"
    assert body["duration_seconds"] is not None


async def test_cancelling_a_completed_session_does_not_change_it(
    authed_client: httpx.AsyncClient
):
    task_id = await _task(authed_client)
    started = (await _start(authed_client, task_id)).json()
    done = (await authed_client.post(f"/api/sessions/{started['id']}/complete")).json()

    after = (await authed_client.post(f"/api/sessions/{started['id']}/cancel")).json()
    assert after["status"] == "completed"
    assert after["ended_at"] == done["ended_at"]


async def test_sessions_are_scoped_to_their_owner(
    authed_client: httpx.AsyncClient, second_user_token: str
):
    task_id = await _task(authed_client)
    started = (await _start(authed_client, task_id)).json()
    headers = {"Authorization": f"Bearer {second_user_token}"}

    assert (await authed_client.get("/api/sessions", headers=headers)).json() == []
    assert (
        await authed_client.post(
            f"/api/sessions/{started['id']}/cancel", headers=headers
        )
    ).status_code == 404


async def test_a_long_overdue_session_cannot_just_be_completed(
    authed_client: httpx.AsyncClient
):
    """Elapsed time stops being evidence of work at some point.

    /complete records ended_at - started_at. Unbounded, a session left running
    with every tab closed records the whole night as focus the next time
    anything claims it — silently, and phase 3 sums it.
    """
    from app.models import Session as SessionModel

    task_id = await _task(authed_client)
    started = (await _start(authed_client, task_id)).json()

    session = await authed_client.session.scalar(  # type: ignore[attr-defined]
        select(SessionModel).where(SessionModel.id == started["id"])
    )
    session.started_at = datetime.now(timezone.utc) - timedelta(hours=9)
    await authed_client.session.commit()  # type: ignore[attr-defined]

    response = await authed_client.post(f"/api/sessions/{started['id']}/complete")
    assert response.status_code == 409, response.text
    assert "planned end" in response.json()["detail"]


async def test_a_briefly_overdue_session_still_completes(
    authed_client: httpx.AsyncClient
):
    """The window has to cover a throttled or briefly asleep tab.

    That is the case the countdown's zero-crossing claim actually hits, so
    refusing it would break the normal path.
    """
    from app.models import Session as SessionModel

    task_id = await _task(authed_client)
    started = (await _start(authed_client, task_id)).json()

    session = await authed_client.session.scalar(  # type: ignore[attr-defined]
        select(SessionModel).where(SessionModel.id == started["id"])
    )
    # 25-minute plan, started 40 minutes ago: 15 minutes overdue.
    session.started_at = datetime.now(timezone.utc) - timedelta(minutes=40)
    await authed_client.session.commit()  # type: ignore[attr-defined]

    response = await authed_client.post(f"/api/sessions/{started['id']}/complete")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"


async def test_an_overdue_session_can_still_be_closed_with_fix_end(
    authed_client: httpx.AsyncClient
):
    # The way out of the 409 above: say when it actually ended.
    from app.models import Session as SessionModel

    task_id = await _task(authed_client)
    started = (await _start(authed_client, task_id)).json()

    session = await authed_client.session.scalar(  # type: ignore[attr-defined]
        select(SessionModel).where(SessionModel.id == started["id"])
    )
    session.started_at = datetime.now(timezone.utc) - timedelta(hours=9)
    await authed_client.session.commit()  # type: ignore[attr-defined]

    fixed = await authed_client.patch(
        f"/api/sessions/{started['id']}", json={"duration_minutes": 25}
    )
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["duration_seconds"] == 25 * 60


async def test_an_overdue_session_can_still_be_cancelled(
    authed_client: httpx.AsyncClient
):
    from app.models import Session as SessionModel

    task_id = await _task(authed_client)
    started = (await _start(authed_client, task_id)).json()

    session = await authed_client.session.scalar(  # type: ignore[attr-defined]
        select(SessionModel).where(SessionModel.id == started["id"])
    )
    session.started_at = datetime.now(timezone.utc) - timedelta(hours=9)
    await authed_client.session.commit()  # type: ignore[attr-defined]

    response = await authed_client.post(f"/api/sessions/{started['id']}/cancel")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"


@pytest.mark.parametrize("path", ["", "/archive"])
async def test_a_task_with_a_running_session_cannot_be_removed(
    authed_client: httpx.AsyncClient, path: str
):
    """Otherwise the session runs against a task no list shows.

    It stays unreachable except through /sessions/active, and blocks every new
    start with a 409. The client disables these buttons, but that reads one
    tab's cache — a second device a few seconds behind would still do it.
    """
    task_id = await _task(authed_client)
    await _start(authed_client, task_id)

    if path:
        response = await authed_client.post(f"/api/tasks/{task_id}{path}")
    else:
        response = await authed_client.delete(f"/api/tasks/{task_id}")
    assert response.status_code == 409, response.text

    # And the task is still there.
    assert [t["id"] for t in (await authed_client.get("/api/tasks")).json()] == [task_id]


@pytest.mark.parametrize("path", ["", "/archive"])
async def test_the_task_can_be_removed_once_the_session_ends(
    authed_client: httpx.AsyncClient, path: str
):
    task_id = await _task(authed_client)
    started = (await _start(authed_client, task_id)).json()
    await authed_client.post(f"/api/sessions/{started['id']}/complete")

    if path:
        response = await authed_client.post(f"/api/tasks/{task_id}{path}")
        assert response.status_code == 200, response.text
    else:
        response = await authed_client.delete(f"/api/tasks/{task_id}")
        assert response.status_code == 204, response.text


async def test_another_users_running_session_does_not_block_my_delete(
    authed_client: httpx.AsyncClient, second_user_token: str
):
    # The EXISTS is correlated on task_id, so it must not see across users.
    headers = {"Authorization": f"Bearer {second_user_token}"}
    theirs = (
        await authed_client.post("/api/tasks", json={"title": "Theirs"}, headers=headers)
    ).json()["id"]
    await authed_client.post(
        "/api/sessions/start", json={"task_id": theirs}, headers=headers
    )

    mine = await _task(authed_client, "Mine")
    assert (await authed_client.delete(f"/api/tasks/{mine}")).status_code == 204


async def test_two_concurrent_starts_yield_one_201_and_one_409(
    engine, real_db_client: httpx.AsyncClient, register_payload: dict[str, str]
):
    """The overlap the partial unique index exists for.

    Every other start test is sequential: the second request runs after the
    first has committed, so an application-level "is anything running?"
    pre-check would pass them too. Here both writers reach their INSERT while
    the other's row is still uncommitted — the window a pre-check cannot
    close.

    Driving this through `authed_client` proves nothing: every request shares
    one AsyncSession, so the second INSERT would be in the same transaction as
    the first and could never conflict with it. Hence two independent sessions
    and the production handler called directly.
    """
    from types import SimpleNamespace

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.api.routes.sessions import _running, start_session
    from app.models import User
    from app.schemas.session import SessionStart
    from app.ws.manager import ConnectionManager

    registered = await real_db_client.post("/api/auth/register", json=register_payload)
    assert registered.status_code == 201, registered.text
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    user_id = UUID(registered.json()["user"]["id"])
    task_id = UUID(
        (
            await real_db_client.post(
                "/api/tasks", json={"title": "Write"}, headers=headers
            )
        ).json()["id"]
    )

    body = SessionStart(task_id=task_id, kind="work")
    # No websocket is registered, so broadcast is a no-op — this only satisfies
    # the handler's dependency on app.state.
    connection = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(ws_manager=ConnectionManager()))
    )

    async with AsyncSession(engine, expire_on_commit=False) as a, AsyncSession(
        engine, expire_on_commit=False
    ) as b:
        user_a = await a.get(User, user_id)
        user_b = await b.get(User, user_id)

        # Both observe an idle timer before either writes. This is the state a
        # read-then-write guard would act on, and it is why the check has to be
        # the index.
        assert await a.scalar(_running(user_a)) is None
        assert await b.scalar(_running(user_b)) is None

        results = await asyncio.gather(
            start_session(body, user_a, a, connection),  # type: ignore[arg-type]
            start_session(body, user_b, b, connection),  # type: ignore[arg-type]
            return_exceptions=True,
        )

    created = [r for r in results if isinstance(r, SessionOut)]
    refused = [r for r in results if isinstance(r, HTTPException)]
    assert len(created) == 1, results
    assert len(refused) == 1, results
    assert refused[0].status_code == 409, refused[0].detail

    async with AsyncSession(engine) as verify:
        running = (
            await verify.scalars(
                select(Session).where(
                    Session.user_id == user_id, Session.status == "running"
                )
            )
        ).all()
    assert [s.id for s in running] == [created[0].id]
