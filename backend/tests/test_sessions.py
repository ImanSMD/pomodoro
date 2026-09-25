"""Timer sessions: the one-running-session constraint, completion and fix-end."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app.models import Session

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
