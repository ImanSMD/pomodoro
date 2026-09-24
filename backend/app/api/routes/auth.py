from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Cookie, HTTPException, Response, status
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from starlette.requests import HTTPConnection

from app.config import Settings
from app.core.cookies import (
    REFRESH_COOKIE_NAME,
    RefreshTokenInvalid,
    clear_refresh_cookie,
    set_refresh_cookie,
)
from app.core.deps import CurrentUser
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.db import DbSession
from app.models import RefreshToken, User
from app.schemas.auth import AccessToken, LoginRequest, RegisterRequest
from app.schemas.user import UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Hashed once at import over a value nobody knows, purely so the
# no-such-account branch of login can do the same work as the real one. See
# the comment in login() for why that matters.
_DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))

# How long after a rotation a replay of the spent token is treated as a
# duplicate rather than a theft. Two tabs restoring at once, React StrictMode,
# or a proxy retrying a POST that timed out all produce a second request
# carrying the cookie the first one just spent — punishing that by wiping the
# family logs the user out for doing nothing wrong.
REFRESH_REUSE_GRACE = timedelta(seconds=10)


class AuthResponse(AccessToken):
    user: UserOut


def _settings(connection: HTTPConnection) -> Settings:
    return connection.app.state.settings


async def _issue_tokens(
    db: DbSession, response: Response, user: User, settings: Settings
) -> AuthResponse:
    raw_refresh = generate_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_refresh_token(raw_refresh),
            expires_at=datetime.now(timezone.utc)
            + timedelta(days=settings.refresh_token_ttl_days),
        )
    )
    await db.commit()

    set_refresh_cookie(response, raw_refresh, settings)
    return AuthResponse(
        access_token=create_access_token(
            user.id, settings.jwt_secret, settings.access_token_ttl_minutes
        ),
        user=UserOut.model_validate(user),
    )


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest, response: Response, db: DbSession, connection: HTTPConnection
) -> AuthResponse:
    settings = _settings(connection)
    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        display_name=body.display_name,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        # Narrowed to the email uniqueness constraint by name — this is what
        # the naming convention in models/base.py buys. A bare `except
        # IntegrityError` would report ck_users_email_length, or any constraint
        # a later phase adds to this table, as "that email is already taken",
        # sending the user off to fix something that is not wrong.
        if "uq_users_email" not in str(exc.orig):
            raise

        # The constraint is the check, not a prior SELECT: two simultaneous
        # registrations of the same address would both pass a read-then-write
        # test and one would then blow up as a 500.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists",
        ) from None

    return await _issue_tokens(db, response, user, settings)


@router.post("/login")
async def login(
    body: LoginRequest, response: Response, db: DbSession, connection: HTTPConnection
) -> AuthResponse:
    settings = _settings(connection)
    user = await db.scalar(select(User).where(User.email == body.email))

    # One generic failure for "no such account" and "wrong password" alike, so
    # this endpoint cannot be used to enumerate registered addresses.
    #
    # The dummy verify is the other half of that: `or` short-circuits, so
    # without it an unknown address would skip argon2 entirely and return in
    # well under a millisecond while a known one paid tens of milliseconds for
    # the KDF. The identical error body would then be undone by a stopwatch.
    if user is None:
        verify_password(body.password, _DUMMY_PASSWORD_HASH)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    if not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    return await _issue_tokens(db, response, user, settings)


@router.post("/refresh")
async def refresh(
    response: Response,
    db: DbSession,
    connection: HTTPConnection,
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
) -> AuthResponse:
    settings = _settings(connection)

    if refresh_token is None:
        raise RefreshTokenInvalid

    record = await db.scalar(
        select(RefreshToken)
        .where(RefreshToken.token_hash == hash_refresh_token(refresh_token))
        # Locked for the duration: without it two requests carrying the same
        # cookie both see revoked_at IS NULL, both rotate, and two live token
        # families exist with no reuse detection fired.
        .with_for_update()
    )
    if record is None:
        raise RefreshTokenInvalid

    now = datetime.now(timezone.utc)

    if record.revoked_at is not None:
        if now - record.revoked_at <= REFRESH_REUSE_GRACE:
            # A duplicate, not a theft: a sibling request rotated this token a
            # moment ago and its Set-Cookie is already on its way to the
            # browser. Revoking the family here would kill the successor that
            # request just issued, leaving the client holding a cookie that is
            # already dead — logged in until the access token expires, then
            # hard-logged-out with no explanation. Fail this one request only,
            # and leave the good cookie alone.
            raise RefreshTokenInvalid(clear_cookie=False)

        # Past the window, a replay is a replay. Either the legitimate user's
        # cookie was stolen or the thief's copy is being used; there is no way
        # to tell which, so revoke every refresh token for this user.
        #
        # Note what this does NOT do: access tokens already issued stay valid
        # until they expire, so a thief who has just exchanged the stolen
        # cookie keeps API access for up to ACCESS_TOKEN_TTL_MINUTES. Cutting
        # that short needs a tokens_valid_after column checked against the
        # token's iat; see the phase-1 document for why it is deliberately not
        # here.
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == record.user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        await db.commit()
        raise RefreshTokenInvalid

    if record.expires_at <= now:
        raise RefreshTokenInvalid

    user = await db.scalar(select(User).where(User.id == record.user_id))
    if user is None:
        raise RefreshTokenInvalid

    await db.execute(
        delete(RefreshToken).where(
            RefreshToken.user_id == record.user_id,
            RefreshToken.expires_at < now,
        )
    )

    # Rotate: this token is spent the moment it is exchanged.
    record.revoked_at = now
    return await _issue_tokens(db, response, user, settings)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    db: DbSession,
    connection: HTTPConnection,
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
) -> None:
    settings = _settings(connection)

    if refresh_token is not None:
        await db.execute(
            update(RefreshToken)
            .where(
                RefreshToken.token_hash == hash_refresh_token(refresh_token),
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(timezone.utc))
        )
        await db.commit()

    # Unconditionally, and always 204: logging out must never fail, and the
    # response must not reveal whether the cookie was a real session. This one
    # works on the injected Response because logout returns normally — the
    # refresh failures raise, which is why they go through the exception
    # handler in main.py instead.
    #
    # This ends the refresh chain, not the current access token: that stays
    # valid until it expires. The client drops it from memory on logout, so
    # this only matters if the token was already captured.
    clear_refresh_cookie(response, settings)


@router.get("/me")
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)
