"""Refresh-cookie handling, in one place so the flags cannot drift apart."""

from __future__ import annotations

from fastapi import Response

from app.config import Settings

REFRESH_COOKIE_NAME = "refresh_token"
# Scoped to the only paths that read it, so the cookie is not attached to every
# API call — a request that does not need it cannot leak it.
REFRESH_COOKIE_PATH = "/api/auth"


def set_refresh_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        token,
        max_age=settings.refresh_token_ttl_days * 24 * 60 * 60,
        # httponly: unreadable from JavaScript, so an XSS cannot exfiltrate a
        # 30-day credential. The access token lives in memory instead.
        httponly=True,
        # lax rather than strict: strict would withhold the cookie on a
        # top-level navigation back into the app, silently logging the user out
        # whenever they arrive from an external link.
        samesite="lax",
        secure=settings.cookie_secure,
        path=REFRESH_COOKIE_PATH,
    )


def clear_refresh_cookie(response: Response, settings: Settings) -> None:
    # Same path and flags as when it was set: a delete that does not match is
    # silently ignored by the browser, leaving the cookie in place.
    response.delete_cookie(
        REFRESH_COOKIE_NAME,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path=REFRESH_COOKIE_PATH,
    )


class RefreshTokenInvalid(Exception):
    """Raised when a refresh token is missing, unknown, expired or replayed.

    A dedicated exception rather than HTTPException because the response must
    also clear the cookie. Deleting it on the injected Response and then
    raising does nothing: FastAPI only merges that Response's headers on the
    non-exception path, and the exception handler builds a fresh one. The
    handler registered in main.py is what actually clears it.

    clear_cookie is False for a benign duplicate refresh, where another
    in-flight request has just installed a good cookie that this response must
    not wipe out. See the grace window in the refresh route.
    """

    def __init__(self, *, clear_cookie: bool = True) -> None:
        super().__init__("Invalid refresh token")
        self.clear_cookie = clear_cookie
