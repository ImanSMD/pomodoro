"""Task CRUD, filtering, reorder, archive and completion."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import httpx
import pytest

pytestmark = pytest.mark.asyncio


async def _task(client: httpx.AsyncClient, title: str, **extra) -> dict:
    response = await client.post("/api/tasks", json={"title": title, **extra})
    assert response.status_code == 201, response.text
    return response.json()


async def test_requires_authentication(client: httpx.AsyncClient):
    assert (await client.get("/api/tasks")).status_code == 401


async def test_create_and_list(authed_client: httpx.AsyncClient):
    await _task(authed_client, "Write the thing")
    listed = (await authed_client.get("/api/tasks")).json()
    assert [t["title"] for t in listed] == ["Write the thing"]
    assert listed[0]["status"] == "active"


async def test_title_is_trimmed_and_blank_is_422(authed_client: httpx.AsyncClient):
    assert (await _task(authed_client, "  Spaced  "))["title"] == "Spaced"
    response = await authed_client.post("/api/tasks", json={"title": "   "})
    assert response.status_code == 422


async def test_create_under_a_category(authed_client: httpx.AsyncClient):
    category = (await authed_client.post("/api/categories", json={"name": "Work"})).json()
    task = await _task(authed_client, "Report", category_id=category["id"])
    assert task["category_id"] == category["id"]

    filtered = await authed_client.get("/api/tasks", params={"category_id": category["id"]})
    assert len(filtered.json()) == 1


async def test_filter_by_status(authed_client: httpx.AsyncClient):
    first = await _task(authed_client, "Done one")
    await _task(authed_client, "Still going")
    await authed_client.post(f"/api/tasks/{first['id']}/complete")

    done = await authed_client.get("/api/tasks", params={"status": "done"})
    assert [t["title"] for t in done.json()] == ["Done one"]

    active = await authed_client.get("/api/tasks", params={"status": "active"})
    assert [t["title"] for t in active.json()] == ["Still going"]


async def test_search_matches_case_insensitively(authed_client: httpx.AsyncClient):
    await _task(authed_client, "Write the REPORT")
    await _task(authed_client, "Read a book")

    found = await authed_client.get("/api/tasks", params={"q": "report"})
    assert [t["title"] for t in found.json()] == ["Write the REPORT"]


@pytest.mark.parametrize("needle", ["%", "_", "100%_done"])
async def test_search_wildcards_are_escaped(
    authed_client: httpx.AsyncClient, needle: str
):
    """A literal % or _ must narrow the search, not widen it.

    Unescaped, "%" matches every row and "_" matches any single character —
    so a user searching for a percent sign would get their whole list back.
    """
    await _task(authed_client, "100%_done")
    await _task(authed_client, "unrelated")

    found = await authed_client.get("/api/tasks", params={"q": needle})
    assert [t["title"] for t in found.json()] == ["100%_done"]


async def test_update_and_patch_semantics(authed_client: httpx.AsyncClient):
    task = await _task(authed_client, "Original", description="keep me")

    patched = await authed_client.patch(
        f"/api/tasks/{task['id']}", json={"title": "Renamed"}
    )
    body = patched.json()
    assert body["title"] == "Renamed"
    assert body["description"] == "keep me"


async def test_clearing_the_category_is_allowed(authed_client: httpx.AsyncClient):
    category = (await authed_client.post("/api/categories", json={"name": "Work"})).json()
    task = await _task(authed_client, "Report", category_id=category["id"])

    patched = await authed_client.patch(
        f"/api/tasks/{task['id']}", json={"category_id": None}
    )
    assert patched.json()["category_id"] is None


async def test_complete_then_reopen(authed_client: httpx.AsyncClient):
    task = await _task(authed_client, "Finish")

    done = (await authed_client.post(f"/api/tasks/{task['id']}/complete")).json()
    assert done["status"] == "done" and done["completed_at"] is not None

    again = (await authed_client.post(f"/api/tasks/{task['id']}/reopen")).json()
    assert again["status"] == "active" and again["completed_at"] is None


async def test_soft_delete_and_archive(authed_client: httpx.AsyncClient):
    kept = await _task(authed_client, "Kept")
    gone = await _task(authed_client, "Gone")

    assert (await authed_client.delete(f"/api/tasks/{gone['id']}")).status_code == 204
    assert [t["title"] for t in (await authed_client.get("/api/tasks")).json()] == ["Kept"]

    await authed_client.post(f"/api/tasks/{kept['id']}/archive")
    assert (await authed_client.get("/api/tasks")).json() == []
    assert len(
        (await authed_client.get("/api/tasks", params={"include_archived": True})).json()
    ) == 1


async def test_reorder_rewrites_positions_densely(authed_client: httpx.AsyncClient):
    a = await _task(authed_client, "A")
    b = await _task(authed_client, "B")
    c = await _task(authed_client, "C")

    response = await authed_client.patch(
        "/api/tasks/reorder", json={"ids": [c["id"], a["id"], b["id"]]}
    )
    assert response.status_code == 204, response.text

    listed = (await authed_client.get("/api/tasks")).json()
    assert [t["title"] for t in listed] == ["C", "A", "B"]
    assert [t["position"] for t in listed] == [0, 1, 2]


async def test_reorder_accepts_a_subset_and_leaves_the_rest_alone(
    authed_client: httpx.AsyncClient
):
    """A subset must work, and must not perturb anything it omits.

    The obvious client renders its drag list from GET /api/tasks?status=active,
    so demanding every live task would make the natural call always 400. Only
    the submitted rows move, among the positions they already occupy.
    """
    a = await _task(authed_client, "A")
    b = await _task(authed_client, "B")
    c = await _task(authed_client, "C")
    d = await _task(authed_client, "D")

    # Swap A and C; B and D are not mentioned at all.
    response = await authed_client.patch(
        "/api/tasks/reorder", json={"ids": [c["id"], a["id"]]}
    )
    assert response.status_code == 204, response.text

    listed = (await authed_client.get("/api/tasks")).json()
    by_title = {t["title"]: t["position"] for t in listed}
    # A and C traded slots 0 and 2; B and D kept 1 and 3 exactly.
    assert by_title == {"C": 0, "B": 1, "A": 2, "D": 3}


async def test_reorder_works_for_the_status_filtered_list(
    authed_client: httpx.AsyncClient
):
    a = await _task(authed_client, "A")
    done = await _task(authed_client, "Done")
    c = await _task(authed_client, "C")
    await authed_client.post(f"/api/tasks/{done['id']}/complete")

    active = (await authed_client.get("/api/tasks", params={"status": "active"})).json()
    response = await authed_client.patch(
        "/api/tasks/reorder", json={"ids": [t["id"] for t in reversed(active)]}
    )
    assert response.status_code == 204, response.text


async def test_reorder_rejects_unknown_ids(authed_client: httpx.AsyncClient):
    await _task(authed_client, "A")
    response = await authed_client.patch(
        "/api/tasks/reorder", json={"ids": ["00000000-0000-0000-0000-000000000000"]}
    )
    assert response.status_code == 400


async def test_reorder_rejects_duplicates(authed_client: httpx.AsyncClient):
    a = await _task(authed_client, "A")
    response = await authed_client.patch(
        "/api/tasks/reorder", json={"ids": [a["id"], a["id"]]}
    )
    assert response.status_code == 400


async def test_reorder_is_not_parsed_as_a_task_id(authed_client: httpx.AsyncClient):
    # /reorder is declared before /{task_id}; otherwise "reorder" would be
    # attempted as a UUID and 422.
    await _task(authed_client, "A")
    listed = (await authed_client.get("/api/tasks")).json()
    response = await authed_client.patch(
        "/api/tasks/reorder", json={"ids": [t["id"] for t in listed]}
    )
    assert response.status_code == 204


async def test_unknown_field_is_422(authed_client: httpx.AsyncClient):
    task = await _task(authed_client, "A")
    response = await authed_client.patch(f"/api/tasks/{task['id']}", json={"titel": "x"})
    assert response.status_code == 422


async def test_a_missing_task_is_404(authed_client: httpx.AsyncClient):
    missing = "00000000-0000-0000-0000-000000000000"
    assert (await authed_client.delete(f"/api/tasks/{missing}")).status_code == 404


async def test_restoring_a_task_appends_rather_than_reclaiming_its_slot(
    authed_client: httpx.AsyncClient
):
    """A stale position would tie with whatever now holds it.

    Archive A, reorder the rest, restore A: resuming position 0 would collide
    with the task that took it and silently perturb the order just set.
    """
    a = await _task(authed_client, "A")
    b = await _task(authed_client, "B")
    c = await _task(authed_client, "C")

    await authed_client.post(f"/api/tasks/{a['id']}/archive")
    await authed_client.patch("/api/tasks/reorder", json={"ids": [c["id"], b["id"]]})
    await authed_client.post(f"/api/tasks/{a['id']}/restore")

    listed = (await authed_client.get("/api/tasks")).json()
    positions = [t["position"] for t in listed]
    assert len(set(positions)) == len(positions), f"positions collide: {listed}"
    assert [t["title"] for t in listed] == ["C", "B", "A"]


async def test_completing_twice_does_not_move_the_timestamp(
    authed_client: httpx.AsyncClient
):
    # Phase 3 buckets by completion day; a double-click must not shift it.
    task = await _task(authed_client, "Finish")

    first = (await authed_client.post(f"/api/tasks/{task['id']}/complete")).json()
    second = (await authed_client.post(f"/api/tasks/{task['id']}/complete")).json()
    assert first["completed_at"] == second["completed_at"]


async def test_null_title_is_422_not_500(authed_client: httpx.AsyncClient):
    task = await _task(authed_client, "A")
    response = await authed_client.patch(f"/api/tasks/{task['id']}", json={"title": None})
    assert response.status_code == 422, response.text


async def test_nullable_fields_can_still_be_cleared(authed_client: httpx.AsyncClient):
    task = await _task(authed_client, "A", description="notes")
    response = await authed_client.patch(
        f"/api/tasks/{task['id']}", json={"description": None}
    )
    assert response.status_code == 200
    assert response.json()["description"] is None


async def test_restoring_a_live_task_is_a_no_op(authed_client: httpx.AsyncClient):
    a = await _task(authed_client, "A")
    await _task(authed_client, "B")
    await _task(authed_client, "C")
    before = (await authed_client.get("/api/tasks")).json()

    await authed_client.post(f"/api/tasks/{a['id']}/restore")

    after = (await authed_client.get("/api/tasks")).json()
    assert [t["title"] for t in after] == [t["title"] for t in before]
    assert [t["position"] for t in after] == [t["position"] for t in before]


async def test_reorder_repairs_a_position_collision(authed_client: httpx.AsyncClient):
    """The create path promises "the next reorder resolves it".

    Redistributing only the submitted slots could not keep that promise —
    sorted([3, 3]) is [3, 3] — so two rows that tied on creation would stay
    tied forever and the list order would remain arbitrary.
    """
    from sqlalchemy import select

    from app.models import Task

    a = await _task(authed_client, "A")
    b = await _task(authed_client, "B")

    session = authed_client.session  # type: ignore[attr-defined]
    for task in await session.scalars(select(Task)):
        task.position = 3
    await session.commit()

    response = await authed_client.patch(
        "/api/tasks/reorder", json={"ids": [b["id"], a["id"]]}
    )
    assert response.status_code == 204, response.text

    listed = (await authed_client.get("/api/tasks")).json()
    assert [t["position"] for t in listed] == [0, 1]
    assert [t["title"] for t in listed] == ["B", "A"]


async def test_an_archived_category_stays_referenceable(
    authed_client: httpx.AsyncClient
):
    """Archiving is reversible, so it must not lose the grouping.

    Detaching (as delete does) would mean restoring the category no longer
    restores what was in it. The cost is a client contract: resolve names from
    GET /api/categories?include_archived=true.
    """
    category = (await authed_client.post("/api/categories", json={"name": "Work"})).json()
    existing = await _task(authed_client, "Report", category_id=category["id"])
    await authed_client.post(f"/api/categories/{category['id']}/archive")

    # The existing task keeps its link.
    tasks = (await authed_client.get("/api/tasks")).json()
    assert [t["category_id"] for t in tasks] == [category["id"]]

    # And new work can still be filed there.
    added = await authed_client.post(
        "/api/tasks", json={"title": "More", "category_id": category["id"]}
    )
    assert added.status_code == 201, added.text

    # Restoring brings the whole grouping back intact.
    await authed_client.post(f"/api/categories/{category['id']}/restore")
    still = (await authed_client.get("/api/tasks")).json()
    assert {t["category_id"] for t in still} == {category["id"]}
    assert existing["id"] in {t["id"] for t in still}


async def test_patching_back_an_unchanged_category_is_allowed(
    authed_client: httpx.AsyncClient
):
    """A client resending the object it read must not be locked out.

    Guards the combination, not one line: an earlier revision both 409'd on
    archived categories and ran the ownership check on mere presence of
    category_id, so the normal read-modify-write cycle failed on an edit that
    never touched the categorisation. Reintroducing either alone still leaves
    this passing; reintroducing both fails it.
    """
    category = (await authed_client.post("/api/categories", json={"name": "Work"})).json()
    task = await _task(authed_client, "Report", category_id=category["id"])
    await authed_client.post(f"/api/categories/{category['id']}/archive")

    response = await authed_client.patch(
        f"/api/tasks/{task['id']}",
        json={"title": "Renamed", "category_id": category["id"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["title"] == "Renamed"


async def test_reorder_does_not_leave_archived_rows_colliding(
    authed_client: httpx.AsyncClient
):
    """Archived rows take part in the densify.

    Otherwise one keeps a stale position that ties with a live row, and the
    archived view's order rests on the created_at tiebreak — which does not
    discriminate rows written in the same transaction.
    """
    a = await _task(authed_client, "A")
    b = await _task(authed_client, "B")
    c = await _task(authed_client, "C")

    await authed_client.post(f"/api/tasks/{a['id']}/archive")
    await authed_client.patch("/api/tasks/reorder", json={"ids": [c["id"], b["id"]]})

    everything = (
        await authed_client.get("/api/tasks", params={"include_archived": True})
    ).json()
    positions = [t["position"] for t in everything]
    assert len(set(positions)) == len(positions), everything


async def test_reorder_rejects_an_absurdly_long_list(authed_client: httpx.AsyncClient):
    response = await authed_client.patch(
        "/api/tasks/reorder",
        json={"ids": ["00000000-0000-0000-0000-000000000000"] * 1001},
    )
    assert response.status_code == 422


async def test_unknown_id_errors_are_truncated(authed_client: httpx.AsyncClient):
    # Echoing every rejected id turned a large request into a far larger
    # response.
    await _task(authed_client, "A")
    ids = [f"00000000-0000-0000-0000-{i:012d}" for i in range(50)]

    response = await authed_client.patch("/api/tasks/reorder", json={"ids": ids})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "and 40 more" in detail, detail
    assert len(detail) < 600, len(detail)


async def test_reopening_a_live_task_is_a_no_op(authed_client: httpx.AsyncClient):
    task = await _task(authed_client, "A")
    first = (await authed_client.get("/api/tasks")).json()[0]
    await authed_client.post(f"/api/tasks/{task['id']}/reopen")
    assert (await authed_client.get("/api/tasks")).json()[0] == first


async def test_apply_once_is_decided_in_the_database_not_by_a_pre_check(
    engine, real_db_client: httpx.AsyncClient, register_payload: dict[str, str]
):
    """Two sessions that both observed 'active' must not both stamp.

    Calls the production helper from two independent sessions with the
    interleaving staged explicitly. Two things this deliberately avoids:

    - Driving it through the API with asyncio.gather does not reproduce the
      race — the in-process ASGI transport does not reliably interleave the
      requests, and such a test passes with the pre-check still in place.
    - Building the UPDATE inside the test proves nothing either; that only
      asserts that SQL WHERE clauses work, and passes no matter what
      _apply_once does.
    """
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.api.routes.tasks import _apply_once
    from app.models import Task

    registered = await real_db_client.post("/api/auth/register", json=register_payload)
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    created = await real_db_client.post(
        "/api/tasks", json={"title": "Finish"}, headers=headers
    )
    task_id = UUID(created.json()["id"])

    first_stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    second_stamp = datetime(2026, 6, 1, tzinfo=timezone.utc)

    async with AsyncSession(engine, expire_on_commit=False) as a, AsyncSession(
        engine, expire_on_commit=False
    ) as b:
        # Both load the row while it is still active — the double-click.
        task_a = await a.scalar(select(Task).where(Task.id == task_id))
        task_b = await b.scalar(select(Task).where(Task.id == task_id))
        assert task_a.status == task_b.status == "active"

        await _apply_once(
            a, task_a, Task.status != "done", status="done", completed_at=first_stamp
        )
        await _apply_once(
            b, task_b, Task.status != "done", status="done", completed_at=second_stamp
        )

        settled = await a.scalar(select(Task.completed_at).where(Task.id == task_id))
        assert settled == first_stamp, "the second writer moved the timestamp"


async def test_description_is_bounded(authed_client: httpx.AsyncClient):
    # Unbounded, one oversized row is serialised into every later list read.
    response = await authed_client.post(
        "/api/tasks", json={"title": "Big", "description": "x" * 10_001}
    )
    assert response.status_code == 422

    ok = await authed_client.post(
        "/api/tasks", json={"title": "Fine", "description": "x" * 10_000}
    )
    assert ok.status_code == 201
