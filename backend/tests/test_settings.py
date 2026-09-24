"""User settings: the defaults a task inherits, and the timezone phase 3 buckets by."""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.asyncio


async def test_settings_require_authentication(client: httpx.AsyncClient):
    assert (await client.get("/api/settings")).status_code == 401
    assert (await client.patch("/api/settings", json={})).status_code == 401


async def test_defaults_match_the_schema(authed_client: httpx.AsyncClient):
    body = (await authed_client.get("/api/settings")).json()

    assert body["timezone"] == "Asia/Tehran"
    assert body["calendar_pref"] == "jalali"
    assert body["default_work_minutes"] == 25
    assert body["default_break_minutes"] == 5
    assert body["long_break_minutes"] == 15
    assert body["rounds_before_long_break"] == 4
    assert body["auto_start_breaks"] is False
    assert body["sound_enabled"] is True


async def test_patch_leaves_omitted_fields_alone(authed_client: httpx.AsyncClient):
    """The PATCH must be a patch.

    model_dump(exclude_unset=True) is what makes this true; exclude_none would
    behave the same here only by accident, and a plain model_dump would reset
    every other setting to None on any single-field change.
    """
    before = (await authed_client.get("/api/settings")).json()

    response = await authed_client.patch(
        "/api/settings", json={"default_work_minutes": 50}
    )
    assert response.status_code == 200, response.text

    after = response.json()
    assert after["default_work_minutes"] == 50
    assert {k: v for k, v in after.items() if k != "default_work_minutes"} == {
        k: v for k, v in before.items() if k != "default_work_minutes"
    }


async def test_patch_persists(authed_client: httpx.AsyncClient):
    await authed_client.patch("/api/settings", json={"calendar_pref": "gregorian"})
    assert (await authed_client.get("/api/settings")).json()["calendar_pref"] == "gregorian"


@pytest.mark.parametrize(
    "payload",
    [
        {"timezone": "Mars/Olympus"},
        {"timezone": ""},
        # zoneinfo lists this, Postgres rejects it: `AT TIME ZONE 'localtime'`
        # is an error, so storing it would break every phase-3 query for the
        # user — the late SQL failure this validator exists to prevent.
        {"timezone": "localtime"},
        # A placeholder meaning "no zone configured"; meaningless as a setting.
        {"timezone": "Factory"},
        {"calendar_pref": "mayan"},
        {"default_work_minutes": 0},
        {"default_work_minutes": 1441},
        {"rounds_before_long_break": 0},
    ],
)
async def test_invalid_settings_are_rejected(
    authed_client: httpx.AsyncClient, payload: dict
):
    # The timezone cases matter beyond tidiness: phase 3 passes this value
    # straight into AT TIME ZONE, where an unknown name surfaces as a SQL
    # error inside an analytics query rather than here.
    response = await authed_client.patch("/api/settings", json=payload)
    assert response.status_code == 422, response.text


async def test_a_real_timezone_is_accepted(authed_client: httpx.AsyncClient):
    response = await authed_client.patch("/api/settings", json={"timezone": "Europe/Berlin"})
    assert response.status_code == 200
    assert response.json()["timezone"] == "Europe/Berlin"


async def test_settings_are_scoped_to_the_caller(
    client: httpx.AsyncClient, register_payload: dict[str, str]
):
    first = await client.post("/api/auth/register", json=register_payload)
    second = await client.post(
        "/api/auth/register",
        json={**register_payload, "email": "grace@example.com", "display_name": "Grace"},
    )

    await client.patch(
        "/api/settings",
        json={"default_work_minutes": 50},
        headers={"Authorization": f"Bearer {first.json()['access_token']}"},
    )

    other = await client.get(
        "/api/settings",
        headers={"Authorization": f"Bearer {second.json()['access_token']}"},
    )
    assert other.json()["default_work_minutes"] == 25


@pytest.mark.parametrize(
    "field",
    ["timezone", "calendar_pref", "default_work_minutes", "sound_enabled"],
)
async def test_an_explicit_null_is_a_422_not_a_500(
    authed_client: httpx.AsyncClient, field: str
):
    """None means "not supplied", never "write NULL".

    Every field is optional so a PATCH can omit it, but no column behind them
    is nullable — so an explicit null used to survive exclude_unset and hit a
    NOT NULL violation as an unhandled 500.
    """
    response = await authed_client.patch("/api/settings", json={field: None})
    assert response.status_code == 422, response.text


async def test_an_unknown_field_is_rejected(authed_client: httpx.AsyncClient):
    # Without extra="forbid" a typo validates, matches nothing, changes
    # nothing and returns 200 — the client cannot tell it was ignored.
    response = await authed_client.patch("/api/settings", json={"work_minutes": 50})
    assert response.status_code == 422


async def test_settings_survive_an_invalid_patch(authed_client: httpx.AsyncClient):
    await authed_client.patch("/api/settings", json={"default_work_minutes": 50})
    await authed_client.patch("/api/settings", json={"timezone": None})

    assert (await authed_client.get("/api/settings")).json()["default_work_minutes"] == 50
