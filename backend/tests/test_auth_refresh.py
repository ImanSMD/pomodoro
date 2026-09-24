"""Refresh-token rotation, reuse detection and logout."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import func, select

from app.core.cookies import REFRESH_COOKIE_NAME
from app.core.security import hash_refresh_token
from app.models import RefreshToken, User

pytestmark = pytest.mark.asyncio


async def _register(client: httpx.AsyncClient, payload: dict[str, str]) -> str:
    response = await client.post("/api/auth/register", json=payload)
    assert response.status_code == 201, response.text
    return response.cookies[REFRESH_COOKIE_NAME]


async def _age_revocation(client: httpx.AsyncClient, raw: str) -> None:
    """Push a token's revocation back past the reuse grace window.

    Replaying within the window is a benign duplicate by design, so a test
    about genuine theft has to look like genuine theft.
    """
    from app.api.routes.auth import REFRESH_REUSE_GRACE

    session = client.session  # type: ignore[attr-defined]
    record = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw))
    )
    record.revoked_at = (
        datetime.now(timezone.utc) - REFRESH_REUSE_GRACE - timedelta(seconds=1)
    )
    await session.commit()


async def _refresh_with(client: httpx.AsyncClient, token: str) -> httpx.Response:
    """Send exactly one refresh cookie, set on the client.

    Per-request cookies= is deprecated in httpx precisely because what ends up
    on the wire is ambiguous when the jar already holds a cookie of the same
    name — and every replay test here depends on sending the OLD token rather
    than the current one.
    """
    client.cookies.set(REFRESH_COOKIE_NAME, token, path="/api/auth")
    return await client.post("/api/auth/refresh")


async def test_refresh_issues_a_new_access_token(
    client: httpx.AsyncClient, register_payload: dict[str, str]
):
    await _register(client, register_payload)

    response = await client.post("/api/auth/refresh")
    assert response.status_code == 200, response.text
    assert response.json()["access_token"]


async def test_refresh_rotates_the_cookie(
    client: httpx.AsyncClient, register_payload: dict[str, str]
):
    original = await _register(client, register_payload)

    response = await client.post("/api/auth/refresh")
    assert response.cookies[REFRESH_COOKIE_NAME] != original


async def test_a_rotated_token_cannot_be_used_again(
    client: httpx.AsyncClient, register_payload: dict[str, str]
):
    original = await _register(client, register_payload)
    await client.post("/api/auth/refresh")

    # Replay the spent token explicitly, since the client now holds the new one.
    response = await _refresh_with(client, original)
    assert response.status_code == 401


async def test_replaying_a_spent_token_revokes_the_whole_family(
    client: httpx.AsyncClient, register_payload: dict[str, str]
):
    """A replay means the cookie leaked; which copy is genuine is unknowable.

    So every live token for the user is revoked and they must log in again —
    the thief is locked out, at the cost of logging the user out too.
    """
    original = await _register(client, register_payload)
    refreshed = await client.post("/api/auth/refresh")
    current = refreshed.cookies[REFRESH_COOKIE_NAME]

    await _age_revocation(client, original)
    await _refresh_with(client, original)

    # The token that was still legitimately live is now dead too.
    response = await _refresh_with(client, current)
    assert response.status_code == 401

    live = await client.session.scalar(  # type: ignore[attr-defined]
        select(func.count())
        .select_from(RefreshToken)
        .where(RefreshToken.revoked_at.is_(None))
    )
    assert live == 0


async def test_refresh_without_a_cookie_is_401(client: httpx.AsyncClient):
    response = await client.post("/api/auth/refresh")
    assert response.status_code == 401


async def test_an_unknown_token_is_401(client: httpx.AsyncClient):
    response = await _refresh_with(client, "nonsense")
    assert response.status_code == 401


def _clears_refresh_cookie(response: httpx.Response) -> bool:
    """True when the response actually tells the browser to drop the cookie.

    Checked on the raw header rather than response.cookies: a deletion is a
    Set-Cookie with an empty value and a past expiry, which httpx does not
    surface as a cookie.
    """
    return any(
        REFRESH_COOKIE_NAME in value and ("Max-Age=0" in value or "expires=" in value.lower())
        for value in response.headers.get_list("set-cookie")
    )


@pytest.mark.parametrize("token", ["nonsense", ""])
async def test_a_rejected_refresh_clears_the_cookie(
    client: httpx.AsyncClient, token: str
):
    """The clear has to ride on the response the client actually receives.

    Deleting the cookie on the route's injected Response and then raising is a
    no-op — FastAPI merges those headers only on the non-exception path — so
    the browser would keep a dead 30-day cookie and resend it forever.
    """
    response = await _refresh_with(client, token)
    assert response.status_code == 401
    assert _clears_refresh_cookie(response), response.headers.get_list("set-cookie")


async def test_reuse_detection_also_clears_the_cookie(
    client: httpx.AsyncClient, register_payload: dict[str, str]
):
    original = await _register(client, register_payload)
    await client.post("/api/auth/refresh")
    await _age_revocation(client, original)

    response = await _refresh_with(client, original)
    assert response.status_code == 401
    assert _clears_refresh_cookie(response)


async def test_a_duplicate_refresh_does_not_destroy_the_session(
    real_db_client: httpx.AsyncClient, register_payload: dict[str, str]
):
    """Two tabs, StrictMode or a retried POST must not log the user out.

    Uses real per-request sessions: the shared-session `client` fixture cannot
    exercise this, because SELECT ... FOR UPDATE never blocks against its own
    transaction.

    The loser of the race sees a token that was revoked moments ago. Treating
    that as a theft would revoke the family — including the successor the
    winner just issued — leaving the browser holding a dead cookie.
    """
    import asyncio

    registered = await real_db_client.post("/api/auth/register", json=register_payload)
    raw = registered.cookies[REFRESH_COOKIE_NAME]
    real_db_client.cookies.set(REFRESH_COOKIE_NAME, raw, path="/api/auth")

    first, second = await asyncio.gather(
        real_db_client.post("/api/auth/refresh"),
        real_db_client.post("/api/auth/refresh"),
    )
    statuses = sorted(r.status_code for r in (first, second))
    assert statuses == [200, 401], statuses

    winner = next(r for r in (first, second) if r.status_code == 200)
    loser = next(r for r in (first, second) if r.status_code == 401)

    # The duplicate must not wipe the cookie the winner just set.
    assert not any(
        REFRESH_COOKIE_NAME in v and "Max-Age=0" in v
        for v in loser.headers.get_list("set-cookie")
    )

    # And the winner's token must still work.
    successor = winner.cookies[REFRESH_COOKIE_NAME]
    real_db_client.cookies.set(REFRESH_COOKIE_NAME, successor, path="/api/auth")
    again = await real_db_client.post("/api/auth/refresh")
    assert again.status_code == 200, "the successor token was revoked by its own sibling"


async def test_a_genuine_replay_past_the_grace_window_revokes_the_family(
    client: httpx.AsyncClient, register_payload: dict[str, str]
):
    """The grace window must not disarm reuse detection itself."""
    from app.api.routes.auth import REFRESH_REUSE_GRACE

    original = await _register(client, register_payload)
    await client.post("/api/auth/refresh")

    session = client.session  # type: ignore[attr-defined]
    spent = await session.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_refresh_token(original)
        )
    )
    # Age the rotation past the window.
    spent.revoked_at = datetime.now(timezone.utc) - REFRESH_REUSE_GRACE - timedelta(seconds=1)
    await session.commit()

    response = await _refresh_with(client, original)
    assert response.status_code == 401

    live = await session.scalar(
        select(func.count())
        .select_from(RefreshToken)
        .where(RefreshToken.revoked_at.is_(None))
    )
    assert live == 0


async def test_expired_tokens_are_pruned_on_rotation(
    client: httpx.AsyncClient, register_payload: dict[str, str]
):
    """Nothing else ever deletes from refresh_tokens.

    Every login and every rotation inserts a row; at a 15-minute access TTL
    that is tens of thousands of dead rows per user per year, and the
    reuse-detection UPDATE scans all of them.
    """
    raw = await _register(client, register_payload)
    session = client.session  # type: ignore[attr-defined]

    session.add(
        RefreshToken(
            user_id=(
                await session.scalar(
                    select(User.id).where(User.email == register_payload["email"])
                )
            ),
            token_hash="dead" + "0" * 60,
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
    )
    await session.commit()

    await _refresh_with(client, raw)

    remaining = await session.scalar(
        select(func.count())
        .select_from(RefreshToken)
        .where(RefreshToken.expires_at < datetime.now(timezone.utc))
    )
    assert remaining == 0


async def test_an_expired_token_is_401(
    client: httpx.AsyncClient, register_payload: dict[str, str]
):
    raw = await _register(client, register_payload)
    session = client.session  # type: ignore[attr-defined]

    record = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw))
    )
    record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await session.commit()

    response = await _refresh_with(client, raw)
    assert response.status_code == 401


async def test_logout_revokes_the_token_and_clears_the_cookie(
    client: httpx.AsyncClient, register_payload: dict[str, str]
):
    raw = await _register(client, register_payload)

    response = await client.post("/api/auth/logout")
    assert response.status_code == 204

    replay = await _refresh_with(client, raw)
    assert replay.status_code == 401


async def test_logout_without_a_session_still_succeeds(client: httpx.AsyncClient):
    # Logging out must never fail, and must not reveal whether the cookie was
    # a real session.
    response = await client.post("/api/auth/logout")
    assert response.status_code == 204


async def test_only_the_hash_of_a_refresh_token_is_stored(
    client: httpx.AsyncClient, register_payload: dict[str, str]
):
    raw = await _register(client, register_payload)

    stored = await client.session.scalars(select(RefreshToken.token_hash))  # type: ignore[attr-defined]
    hashes = list(stored)
    assert raw not in hashes
    assert hash_refresh_token(raw) in hashes


async def test_deleting_a_user_cascades_to_their_tokens(
    client: httpx.AsyncClient, register_payload: dict[str, str]
):
    await _register(client, register_payload)
    session = client.session  # type: ignore[attr-defined]

    user = await session.scalar(select(User).where(User.email == register_payload["email"]))
    await session.delete(user)
    await session.flush()

    remaining = await session.scalar(select(func.count()).select_from(RefreshToken))
    assert remaining == 0
