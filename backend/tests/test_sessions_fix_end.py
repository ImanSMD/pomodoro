"""fix-end: closing a session you walked away from, as the old CLI did."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

pytestmark = pytest.mark.asyncio


async def _running(client: httpx.AsyncClient, started_hours_ago: float = 2) -> dict:
    """A session begun a while back and never stopped.

    Backdated because that is what fix-end is for — you walked away without
    ending it, and now you say when it really finished. A session started a
    moment ago cannot legitimately be given an end time an hour out.
    """
    from sqlalchemy import select

    from app.models import Session as SessionModel

    task = await client.post("/api/tasks", json={"title": "Write"})
    started = await client.post(
        "/api/sessions/start", json={"task_id": task.json()["id"]}
    )
    assert started.status_code == 201, started.text

    session = await client.session.scalar(  # type: ignore[attr-defined]
        select(SessionModel).where(SessionModel.id == started.json()["id"])
    )
    session.started_at = datetime.now(timezone.utc) - timedelta(hours=started_hours_ago)
    await client.session.commit()  # type: ignore[attr-defined]

    refreshed = await client.get("/api/sessions")
    return refreshed.json()[0]


async def test_duration_closes_the_session(authed_client: httpx.AsyncClient):
    session = await _running(authed_client)

    body = (
        await authed_client.patch(
            f"/api/sessions/{session['id']}", json={"duration_minutes": 45}
        )
    ).json()
    assert body["status"] == "completed"
    assert body["duration_seconds"] == 45 * 60

    started = datetime.fromisoformat(body["started_at"])
    assert datetime.fromisoformat(body["ended_at"]) == started + timedelta(minutes=45)


async def test_explicit_end_time_closes_the_session(authed_client: httpx.AsyncClient):
    session = await _running(authed_client)
    ended = datetime.fromisoformat(session["started_at"]) + timedelta(minutes=30)

    body = (
        await authed_client.patch(
            f"/api/sessions/{session['id']}", json={"ended_at": ended.isoformat()}
        )
    ).json()
    assert body["status"] == "completed"
    assert body["duration_seconds"] == 30 * 60


async def test_giving_both_is_rejected(authed_client: httpx.AsyncClient):
    # There is no sensible rule for which wins when the two disagree.
    session = await _running(authed_client)
    ended = datetime.now(timezone.utc)

    response = await authed_client.patch(
        f"/api/sessions/{session['id']}",
        json={"ended_at": ended.isoformat(), "duration_minutes": 10},
    )
    assert response.status_code == 422


async def test_an_end_before_the_start_is_rejected(authed_client: httpx.AsyncClient):
    session = await _running(authed_client)
    before = datetime.fromisoformat(session["started_at"]) - timedelta(minutes=5)

    response = await authed_client.patch(
        f"/api/sessions/{session['id']}", json={"ended_at": before.isoformat()}
    )
    # Caught in the route so it is an explained 422, not a 500 from
    # ck_sessions_ends_after_start.
    assert response.status_code == 422, response.text


async def test_a_naive_timestamp_is_rejected(authed_client: httpx.AsyncClient):
    """Without an offset there is no way to know what instant is meant.

    Phase 3 buckets by the user's local day, so guessing here would quietly
    misfile the session.
    """
    session = await _running(authed_client)
    naive = datetime.now().replace(tzinfo=None)

    response = await authed_client.patch(
        f"/api/sessions/{session['id']}", json={"ended_at": naive.isoformat()}
    )
    assert response.status_code == 422, response.text


async def test_a_cancelled_session_cannot_be_given_an_end(
    authed_client: httpx.AsyncClient
):
    session = await _running(authed_client)
    await authed_client.post(f"/api/sessions/{session['id']}/cancel")

    response = await authed_client.patch(
        f"/api/sessions/{session['id']}", json={"duration_minutes": 20}
    )
    assert response.status_code == 409, response.text


async def test_a_note_can_be_added_without_closing(authed_client: httpx.AsyncClient):
    session = await _running(authed_client)

    body = (
        await authed_client.patch(
            f"/api/sessions/{session['id']}", json={"note": "interrupted by a call"}
        )
    ).json()
    assert body["note"] == "interrupted by a call"
    assert body["status"] == "running"


async def test_an_empty_patch_is_rejected(authed_client: httpx.AsyncClient):
    session = await _running(authed_client)
    response = await authed_client.patch(f"/api/sessions/{session['id']}", json={})
    assert response.status_code == 422


@pytest.mark.parametrize("minutes", [0, -5, 24 * 60 + 1])
async def test_implausible_durations_are_rejected(
    authed_client: httpx.AsyncClient, minutes: float
):
    session = await _running(authed_client)
    response = await authed_client.patch(
        f"/api/sessions/{session['id']}", json={"duration_minutes": minutes}
    )
    assert response.status_code == 422


async def test_closing_frees_the_running_slot(authed_client: httpx.AsyncClient):
    session = await _running(authed_client)
    await authed_client.patch(
        f"/api/sessions/{session['id']}", json={"duration_minutes": 25}
    )

    task = await authed_client.post("/api/tasks", json={"title": "Next"})
    again = await authed_client.post(
        "/api/sessions/start", json={"task_id": task.json()["id"]}
    )
    assert again.status_code == 201, again.text


async def test_an_end_time_in_the_future_is_rejected(authed_client: httpx.AsyncClient):
    """Fix-end is retrospective — you say when it *did* end.

    Unbounded, one PATCH writes a year of "focus time" that phase 3 sums, and
    frees the running slot while leaving a completed session overlapping every
    later one. Reachable without malice: the client computes this value and
    its clock may be skewed.
    """
    session = await _running(authed_client)
    far_future = datetime.now(timezone.utc) + timedelta(days=365)

    response = await authed_client.patch(
        f"/api/sessions/{session['id']}", json={"ended_at": far_future.isoformat()}
    )
    assert response.status_code == 422, response.text


async def test_a_duration_that_ends_in_the_future_is_rejected(
    authed_client: httpx.AsyncClient
):
    # "It ran 90 minutes" on a session started 10 minutes ago is the same
    # claim as an ended_at in the future; both ways must be bounded alike.
    session = await _running(authed_client, started_hours_ago=0.1)

    response = await authed_client.patch(
        f"/api/sessions/{session['id']}", json={"duration_minutes": 90}
    )
    assert response.status_code == 422, response.text


async def test_a_session_longer_than_a_day_is_rejected(
    authed_client: httpx.AsyncClient
):
    session = await _running(authed_client, started_hours_ago=30)
    ended = datetime.now(timezone.utc)

    response = await authed_client.patch(
        f"/api/sessions/{session['id']}", json={"ended_at": ended.isoformat()}
    )
    assert response.status_code == 422, response.text


async def test_a_note_can_be_cleared(authed_client: httpx.AsyncClient):
    """Explicit null means clear, not "not supplied".

    Otherwise a note, once attached, has no API path to remove it.
    """
    session = await _running(authed_client)
    await authed_client.patch(
        f"/api/sessions/{session['id']}", json={"note": "interrupted"}
    )

    cleared = await authed_client.patch(
        f"/api/sessions/{session['id']}", json={"note": None}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["note"] is None
