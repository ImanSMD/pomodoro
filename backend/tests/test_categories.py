"""Category CRUD, soft delete, archive and the case-insensitive uniqueness rule."""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.asyncio


async def test_requires_authentication(client: httpx.AsyncClient):
    assert (await client.get("/api/categories")).status_code == 401


async def test_create_and_list(authed_client: httpx.AsyncClient):
    created = await authed_client.post(
        "/api/categories", json={"name": "Deep Work", "color": "#f00", "icon": "brain"}
    )
    assert created.status_code == 201, created.text
    assert created.json()["name"] == "Deep Work"

    listed = (await authed_client.get("/api/categories")).json()
    assert [c["name"] for c in listed] == ["Deep Work"]


async def test_name_is_trimmed(authed_client: httpx.AsyncClient):
    created = await authed_client.post("/api/categories", json={"name": "  Reading  "})
    assert created.json()["name"] == "Reading"


@pytest.mark.parametrize("name", ["", "   ", "\t\n"])
async def test_blank_name_is_422(authed_client: httpx.AsyncClient, name: str):
    # Mirrors ck_categories_name_not_blank: without the schema check this
    # would reach the database and fail as a 500.
    response = await authed_client.post("/api/categories", json={"name": name})
    assert response.status_code == 422


async def test_duplicate_name_is_409_case_insensitively(authed_client: httpx.AsyncClient):
    await authed_client.post("/api/categories", json={"name": "Work"})
    response = await authed_client.post("/api/categories", json={"name": "WORK"})
    assert response.status_code == 409


async def test_a_soft_deleted_name_can_be_reused(authed_client: httpx.AsyncClient):
    """The uniqueness index is partial for exactly this reason.

    Without `WHERE deleted_at IS NULL` a deleted category would permanently
    reserve its name against a user who can no longer see it.
    """
    first = await authed_client.post("/api/categories", json={"name": "Work"})
    await authed_client.delete(f"/api/categories/{first.json()['id']}")

    again = await authed_client.post("/api/categories", json={"name": "work"})
    assert again.status_code == 201, again.text


async def test_two_users_may_share_a_name(
    authed_client: httpx.AsyncClient, second_user_token: str
):
    await authed_client.post("/api/categories", json={"name": "Work"})
    response = await authed_client.post(
        "/api/categories",
        json={"name": "Work"},
        headers={"Authorization": f"Bearer {second_user_token}"},
    )
    assert response.status_code == 201, response.text


async def test_rename_to_an_existing_name_is_409(authed_client: httpx.AsyncClient):
    await authed_client.post("/api/categories", json={"name": "Work"})
    other = await authed_client.post("/api/categories", json={"name": "Rest"})

    response = await authed_client.patch(
        f"/api/categories/{other.json()['id']}", json={"name": "work"}
    )
    assert response.status_code == 409


async def test_patch_leaves_omitted_fields_alone(authed_client: httpx.AsyncClient):
    created = (
        await authed_client.post(
            "/api/categories", json={"name": "Work", "color": "#abc", "icon": "cog"}
        )
    ).json()

    patched = await authed_client.patch(
        f"/api/categories/{created['id']}", json={"name": "Focus"}
    )
    body = patched.json()
    assert body["name"] == "Focus"
    assert body["color"] == "#abc" and body["icon"] == "cog"


async def test_unknown_field_is_422(authed_client: httpx.AsyncClient):
    created = (await authed_client.post("/api/categories", json={"name": "Work"})).json()
    response = await authed_client.patch(
        f"/api/categories/{created['id']}", json={"colour": "#abc"}
    )
    assert response.status_code == 422


async def test_soft_delete_hides_it_from_the_list(authed_client: httpx.AsyncClient):
    created = (await authed_client.post("/api/categories", json={"name": "Work"})).json()

    assert (await authed_client.delete(f"/api/categories/{created['id']}")).status_code == 204
    assert (await authed_client.get("/api/categories")).json() == []
    # And it stays gone on a direct hit, rather than 403-ing or erroring.
    assert (
        await authed_client.patch(f"/api/categories/{created['id']}", json={"name": "x"})
    ).status_code == 404


async def test_archive_and_restore(authed_client: httpx.AsyncClient):
    created = (await authed_client.post("/api/categories", json={"name": "Work"})).json()

    archived = await authed_client.post(f"/api/categories/{created['id']}/archive")
    assert archived.json()["archived_at"] is not None
    assert (await authed_client.get("/api/categories")).json() == []

    with_archived = await authed_client.get(
        "/api/categories", params={"include_archived": True}
    )
    assert len(with_archived.json()) == 1

    restored = await authed_client.post(f"/api/categories/{created['id']}/restore")
    assert restored.json()["archived_at"] is None
    assert len((await authed_client.get("/api/categories")).json()) == 1


async def test_positions_are_assigned_in_creation_order(authed_client: httpx.AsyncClient):
    for name in ("A", "B", "C"):
        await authed_client.post("/api/categories", json={"name": name})

    listed = (await authed_client.get("/api/categories")).json()
    assert [c["position"] for c in listed] == [0, 1, 2]


async def test_a_missing_category_is_404(authed_client: httpx.AsyncClient):
    missing = "00000000-0000-0000-0000-000000000000"
    assert (await authed_client.delete(f"/api/categories/{missing}")).status_code == 404


async def test_null_name_is_422_not_500(authed_client: httpx.AsyncClient):
    """The validator used to call .strip() on None.

    AttributeError is not a ValueError, so pydantic let it escape FastAPI's
    body parsing as a 500 rather than converting it to a 422.
    """
    created = (await authed_client.post("/api/categories", json={"name": "Work"})).json()
    response = await authed_client.patch(
        f"/api/categories/{created['id']}", json={"name": None}
    )
    assert response.status_code == 422, response.text


async def test_nullable_fields_can_still_be_cleared(authed_client: httpx.AsyncClient):
    created = (
        await authed_client.post("/api/categories", json={"name": "Work", "color": "#abc"})
    ).json()
    response = await authed_client.patch(
        f"/api/categories/{created['id']}", json={"color": None}
    )
    assert response.status_code == 200
    assert response.json()["color"] is None


async def test_soft_deleting_a_category_detaches_its_tasks(
    authed_client: httpx.AsyncClient
):
    """Otherwise the task keeps an id no endpoint resolves.

    ON DELETE SET NULL never fires for a soft delete, so without an explicit
    update the UI renders a category it cannot name — and since the name
    becomes reusable, a new category with the same name leaves those tasks
    attached to the dead one, invisible under both.
    """
    category = (await authed_client.post("/api/categories", json={"name": "Work"})).json()
    task = await authed_client.post(
        "/api/tasks", json={"title": "Report", "category_id": category["id"]}
    )
    assert task.status_code == 201

    await authed_client.delete(f"/api/categories/{category['id']}")

    tasks = (await authed_client.get("/api/tasks")).json()
    assert [t["category_id"] for t in tasks] == [None]


async def test_an_archived_name_conflict_says_so(authed_client: httpx.AsyncClient):
    """The uniqueness index covers archived rows the default listing omits.

    A bare "already exists" would point at a category the user cannot see and
    has no obvious way to find.
    """
    created = (await authed_client.post("/api/categories", json={"name": "Work"})).json()
    await authed_client.post(f"/api/categories/{created['id']}/archive")

    response = await authed_client.post("/api/categories", json={"name": "work"})
    assert response.status_code == 409
    assert "archived" in response.json()["detail"].lower(), response.text


async def test_restoring_a_live_category_is_a_no_op(authed_client: httpx.AsyncClient):
    # The position append must not fire for something that was never archived,
    # or the sidebar reshuffles on a stray restore.
    for name in ("A", "B", "C"):
        await authed_client.post("/api/categories", json={"name": name})
    before = (await authed_client.get("/api/categories")).json()

    await authed_client.post(f"/api/categories/{before[0]['id']}/restore")

    after = (await authed_client.get("/api/categories")).json()
    assert [c["name"] for c in after] == [c["name"] for c in before]
    assert [c["position"] for c in after] == [c["position"] for c in before]
