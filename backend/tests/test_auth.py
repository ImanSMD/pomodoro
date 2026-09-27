"""Registration, login, token rotation and the boundaries around them."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app.core.cookies import REFRESH_COOKIE_NAME
from app.core.security import create_access_token, hash_refresh_token
from app.models import RefreshToken, User

pytestmark = pytest.mark.asyncio


async def test_register_returns_user_and_token(
    client: httpx.AsyncClient, register_payload: dict[str, str]
):
    response = await client.post("/api/auth/register", json=register_payload)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user"]["email"] == register_payload["email"]
    assert body["access_token"]
    assert REFRESH_COOKIE_NAME in response.cookies


async def test_register_never_returns_the_password_hash(
    client: httpx.AsyncClient, register_payload: dict[str, str]
):
    response = await client.post("/api/auth/register", json=register_payload)
    assert "password" not in response.text.lower()


async def test_duplicate_email_is_rejected_case_insensitively(
    client: httpx.AsyncClient, register_payload: dict[str, str]
):
    await client.post("/api/auth/register", json=register_payload)
    shouted = {**register_payload, "email": register_payload["email"].upper()}

    response = await client.post("/api/auth/register", json=shouted)
    assert response.status_code == 409


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("email", "not-an-email"),
        ("email", "a" * 320 + "@example.com"),
        ("password", "short"),
        ("display_name", ""),
    ],
)
async def test_invalid_registration_is_a_422_not_a_500(
    client: httpx.AsyncClient, register_payload: dict[str, str], field: str, value: str
):
    # The over-long email matters most: without the schema bound it would reach
    # the database and fail inside the btree index as a 500.
    response = await client.post(
        "/api/auth/register", json={**register_payload, field: value}
    )
    assert response.status_code == 422


async def test_login_succeeds_with_the_right_password(
    client: httpx.AsyncClient, register_payload: dict[str, str]
):
    await client.post("/api/auth/register", json=register_payload)

    response = await client.post(
        "/api/auth/login",
        json={"email": register_payload["email"], "password": register_payload["password"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["access_token"]


async def test_wrong_password_and_unknown_email_are_indistinguishable(
    client: httpx.AsyncClient, register_payload: dict[str, str]
):
    await client.post("/api/auth/register", json=register_payload)

    wrong_password = await client.post(
        "/api/auth/login",
        json={"email": register_payload["email"], "password": "not-the-password"},
    )
    unknown_email = await client.post(
        "/api/auth/login",
        json={"email": "nobody@example.com", "password": "not-the-password"},
    )

    # Identical status AND body: any difference turns this endpoint into an
    # oracle for which addresses are registered.
    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()


async def test_me_requires_a_token(client: httpx.AsyncClient):
    response = await client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


async def test_me_returns_the_authenticated_user(
    authed_client: httpx.AsyncClient, register_payload: dict[str, str]
):
    response = await authed_client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["email"] == register_payload["email"]


@pytest.mark.parametrize(
    "header",
    ["Bearer not-a-jwt", "Bearer ", "Basic abc", "not-even-a-scheme"],
)
async def test_malformed_credentials_are_rejected(
    client: httpx.AsyncClient, header: str
):
    response = await client.get("/api/auth/me", headers={"Authorization": header})
    assert response.status_code == 401


async def test_an_expired_access_token_is_rejected(
    authed_client: httpx.AsyncClient, test_settings, register_payload: dict[str, str]
):
    user = await authed_client.session.scalar(  # type: ignore[attr-defined]
        select(User).where(User.email == register_payload["email"])
    )
    expired = create_access_token(user.id, test_settings.jwt_secret, ttl_minutes=-1)

    response = await authed_client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {expired}"}
    )
    assert response.status_code == 401


async def test_a_token_signed_with_another_secret_is_rejected(
    authed_client: httpx.AsyncClient, register_payload: dict[str, str]
):
    user = await authed_client.session.scalar(  # type: ignore[attr-defined]
        select(User).where(User.email == register_payload["email"])
    )
    forged = create_access_token(user.id, "a" * 64, ttl_minutes=15)

    response = await authed_client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {forged}"}
    )
    assert response.status_code == 401


async def test_login_does_equal_work_for_known_and_unknown_emails(
    client: httpx.AsyncClient, register_payload: dict[str, str]
):
    """The generic 401 body is only half the anti-enumeration defence.

    `or` short-circuits, so without the dummy verify the unknown-email branch
    skips argon2 and returns in a fraction of the time a known address takes —
    an attacker with a stopwatch enumerates accounts regardless of the message.
    """
    import time

    await client.post("/api/auth/register", json=register_payload)

    async def timed(email: str) -> float:
        start = time.perf_counter()
        response = await client.post(
            "/api/auth/login", json={"email": email, "password": "wrong-password"}
        )
        assert response.status_code == 401
        return time.perf_counter() - start

    # Several samples each, taking the minimum: the floor is the work actually
    # done, with scheduling noise removed.
    # List comprehensions, not generator expressions: an `await` inside a
    # genexp makes it an async generator, which min() cannot consume.
    known = min([await timed(register_payload["email"]) for _ in range(5)])
    unknown = min([await timed("nobody@example.com") for _ in range(5)])

    # Argon2 dominates both paths, so they land within the same order of
    # magnitude. Before the fix the ratio was ~50x.
    ratio = max(known, unknown) / min(known, unknown)
    assert ratio < 3, f"timing oracle: known={known:.4f}s unknown={unknown:.4f}s ratio={ratio:.1f}x"


@pytest.mark.parametrize(
    ("path", "extra"),
    [
        ("/api/auth/register", {"timezone": "Europe/Berlin"}),
        ("/api/auth/register", {"default_work_minutes": 90}),
        ("/api/auth/login", {"bogus": "x"}),
    ],
)
async def test_unknown_fields_are_rejected_not_ignored(
    client: httpx.AsyncClient, register_payload: dict[str, str], path: str, extra: dict
):
    """The two auth schemas were the only input models without extra="forbid".

    A sign-up form posting the browser's detected timezone — a real column on
    users, and the most natural extra field on this endpoint — got a 201 and an
    account on the Asia/Tehran default, with nothing saying the field was
    dropped. It feeds phase 3's AT TIME ZONE bucketing, so a wrong value is
    durable and silent.
    """
    if path.endswith("login"):
        assert (
            await client.post("/api/auth/register", json=register_payload)
        ).status_code == 201
        body = {
            "email": register_payload["email"],
            "password": register_payload["password"],
            **extra,
        }
    else:
        body = {**register_payload, **extra}

    response = await client.post(path, json=body)
    assert response.status_code == 422, response.text
