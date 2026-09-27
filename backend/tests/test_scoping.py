"""Cross-user isolation.

The one test that matters most in this phase: every read of an owned table is
supposed to go through scoped(), and the failure when it doesn't is silent in
both directions — one user reads another's data, or a soft-deleted row comes
back from the dead.
"""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.asyncio


async def _make_category(client: httpx.AsyncClient, name: str = "Work") -> str:
    response = await client.post("/api/categories", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _make_task(client: httpx.AsyncClient, title: str = "Write") -> str:
    response = await client.post("/api/tasks", json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_categories_are_invisible_to_another_user(
    authed_client: httpx.AsyncClient, second_user_token: str
):
    await _make_category(authed_client)

    other = await authed_client.get(
        "/api/categories", headers={"Authorization": f"Bearer {second_user_token}"}
    )
    assert other.json() == []


async def test_tasks_are_invisible_to_another_user(
    authed_client: httpx.AsyncClient, second_user_token: str
):
    await _make_task(authed_client)

    other = await authed_client.get(
        "/api/tasks", headers={"Authorization": f"Bearer {second_user_token}"}
    )
    assert other.json() == []


@pytest.mark.parametrize(
    ("method", "path_template"),
    [
        ("PATCH", "/api/categories/{id}"),
        ("DELETE", "/api/categories/{id}"),
        ("POST", "/api/categories/{id}/archive"),
        ("POST", "/api/categories/{id}/restore"),
    ],
)
async def test_another_users_category_is_404_not_403(
    authed_client: httpx.AsyncClient,
    second_user_token: str,
    method: str,
    path_template: str,
):
    # 404, never 403: a 403 confirms the id exists, turning every detail
    # endpoint into an oracle for enumerating other people's rows.
    category_id = await _make_category(authed_client)

    response = await authed_client.request(
        method,
        path_template.format(id=category_id),
        json={} if method == "PATCH" else None,
        headers={"Authorization": f"Bearer {second_user_token}"},
    )
    assert response.status_code == 404, response.text


@pytest.mark.parametrize(
    ("method", "path_template"),
    [
        ("PATCH", "/api/tasks/{id}"),
        ("DELETE", "/api/tasks/{id}"),
        ("POST", "/api/tasks/{id}/archive"),
        ("POST", "/api/tasks/{id}/restore"),
        ("POST", "/api/tasks/{id}/complete"),
        ("POST", "/api/tasks/{id}/reopen"),
    ],
)
async def test_another_users_task_is_404_not_403(
    authed_client: httpx.AsyncClient,
    second_user_token: str,
    method: str,
    path_template: str,
):
    task_id = await _make_task(authed_client)

    response = await authed_client.request(
        method,
        path_template.format(id=task_id),
        json={} if method == "PATCH" else None,
        headers={"Authorization": f"Bearer {second_user_token}"},
    )
    assert response.status_code == 404, response.text


async def test_a_task_cannot_be_filed_under_another_users_category(
    authed_client: httpx.AsyncClient, second_user_token: str
):
    """The foreign key alone would accept it.

    category_id is caller-supplied; without an ownership check the FK is
    satisfied by any existing category, leaking its existence and filing this
    task under someone else's grouping.
    """
    category_id = await _make_category(authed_client)

    response = await authed_client.post(
        "/api/tasks",
        json={"title": "Sneaky", "category_id": category_id},
        headers={"Authorization": f"Bearer {second_user_token}"},
    )
    assert response.status_code == 404, response.text


async def test_a_task_cannot_be_moved_into_another_users_category(
    authed_client: httpx.AsyncClient, second_user_token: str
):
    category_id = await _make_category(authed_client)

    other_task = await authed_client.post(
        "/api/tasks",
        json={"title": "Theirs"},
        headers={"Authorization": f"Bearer {second_user_token}"},
    )
    response = await authed_client.patch(
        f"/api/tasks/{other_task.json()['id']}",
        json={"category_id": category_id},
        headers={"Authorization": f"Bearer {second_user_token}"},
    )
    assert response.status_code == 404, response.text


async def test_reorder_cannot_touch_another_users_tasks(
    authed_client: httpx.AsyncClient, second_user_token: str
):
    mine = await _make_task(authed_client, "Mine")

    response = await authed_client.patch(
        "/api/tasks/reorder",
        json={"ids": [mine]},
        headers={"Authorization": f"Bearer {second_user_token}"},
    )
    # The other user has no tasks, so a list containing mine is not their
    # complete set — and must not silently reposition my row.
    assert response.status_code == 400, response.text


async def _start_session(client: httpx.AsyncClient) -> str:
    task_id = await _make_task(client, "Focus")
    response = await client.post("/api/sessions/start", json={"task_id": task_id})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_sessions_are_invisible_to_another_user(
    authed_client: httpx.AsyncClient, second_user_token: str
):
    await _start_session(authed_client)
    headers = {"Authorization": f"Bearer {second_user_token}"}

    listed = await authed_client.get("/api/sessions", headers=headers)
    assert listed.json() == []

    # And the running one does not leak through the endpoint the timer polls —
    # this is the one read that is not filtered by an id the caller supplied.
    active = await authed_client.get("/api/sessions/active", headers=headers)
    assert active.json()["session"] is None, active.text


@pytest.mark.parametrize(
    ("method", "path_template"),
    [
        ("POST", "/api/sessions/{id}/complete"),
        ("POST", "/api/sessions/{id}/cancel"),
        ("PATCH", "/api/sessions/{id}"),
    ],
)
async def test_another_users_session_is_404_not_403(
    authed_client: httpx.AsyncClient,
    second_user_token: str,
    method: str,
    path_template: str,
):
    session_id = await _start_session(authed_client)

    response = await authed_client.request(
        method,
        path_template.format(id=session_id),
        json={"duration_minutes": 5} if method == "PATCH" else None,
        headers={"Authorization": f"Bearer {second_user_token}"},
    )
    assert response.status_code == 404, response.text


async def test_another_users_session_is_untouched_by_the_attempt(
    authed_client: httpx.AsyncClient, second_user_token: str
):
    """The 404 must come from the read, not from a write that already landed.

    apply_once issues UPDATE ... WHERE id = :id with no user_id predicate — it
    is safe only because every caller passes a row get_owned_or_404 already
    proved is theirs. If that ordering were ever inverted the 404 would still
    be returned, and this is what would notice.
    """
    session_id = await _start_session(authed_client)
    headers = {"Authorization": f"Bearer {second_user_token}"}

    assert (
        await authed_client.post(f"/api/sessions/{session_id}/cancel", headers=headers)
    ).status_code == 404

    mine = await authed_client.get("/api/sessions/active")
    assert mine.json()["session"]["id"] == session_id, mine.text
    assert mine.json()["session"]["status"] == "running"
