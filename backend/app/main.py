from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncio
import time

import jwt
from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select, text
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import (
    auth,
    categories,
    sessions as session_routes,
    settings as settings_routes,
    tasks,
)
from app.config import Settings, get_settings
from app.core.cookies import RefreshTokenInvalid, clear_refresh_cookie
from app.core.security import decode_access_token
from app.models import User
from app.ws.manager import ConnectionManager
from app.db import create_engine, create_session_factory


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    engine = create_engine(settings.database_url)
    try:
        # create_async_engine is lazy — it opens nothing. Probe once here so a
        # bad DATABASE_URL or an unreachable database fails startup loudly,
        # instead of logging "Application startup complete", serving
        # /api/health 200, and 500ing on the first data route.
        #
        # Inside the try so a failed probe still disposes: a supervisor or
        # --reload loop retrying against a briefly unreachable database would
        # otherwise leak an engine per attempt.
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        app.state.ws_manager = ConnectionManager()
        yield
    finally:
        await engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build an app instance.

    A factory rather than a module-level app so tests can pass a Settings
    pointing at the test database without mutating the process environment.
    """
    settings = settings or get_settings()

    app = FastAPI(title="Pomodoro API", lifespan=lifespan)
    app.state.settings = settings

    # Origins are resolved and validated in Settings — an empty list, a
    # wildcard, and a dev fallback under a deployed config are all rejected
    # there rather than silently served here.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,  # the refresh-token cookie rides on these
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.exception_handler(RefreshTokenInvalid)
    async def _refresh_token_invalid(
        request: Request, exc: RefreshTokenInvalid
    ) -> JSONResponse:
        """Clear the cookie on the response the client actually receives.

        Doing it on the route's injected Response and then raising is a no-op:
        those headers are merged only on the non-exception path. Without this
        the browser keeps a dead 30-day cookie, resends it on every refresh,
        and never reaches a clean logged-out state.
        """
        response = JSONResponse(
            status_code=401, content={"detail": "Invalid refresh token"}
        )
        if exc.clear_cookie:
            clear_refresh_cookie(response, request.app.state.settings)
        return response

    app.include_router(auth.router)
    app.include_router(settings_routes.router)
    app.include_router(categories.router)
    app.include_router(tasks.router)
    app.include_router(session_routes.router)

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket, token: str = Query()) -> None:
        """Push session changes to this user's other tabs and devices.

        The token arrives as a query parameter because a browser cannot set
        headers on a WebSocket handshake — there is no way to send
        Authorization here. It is an access token (minutes), not the refresh
        cookie, so the exposure in proxy and server logs is bounded; a
        subprotocol-based scheme would avoid even that if it ever matters.
        """
        settings: Settings = websocket.app.state.settings
        try:
            payload = decode_access_token(token, settings.jwt_secret)
        except jwt.PyJWTError:
            # Closed before accept, so no handshake completes for a caller who
            # cannot prove who they are.
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        session_factory = websocket.app.state.session_factory
        async with session_factory() as db:
            user = await db.scalar(select(User).where(User.id == payload["sub"]))
        if user is None:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        manager: ConnectionManager = websocket.app.state.ws_manager
        await websocket.accept()
        await manager.connect(user.id, websocket)
        try:
            while True:
                # The socket outlives nothing: it closes when the token that
                # opened it expires. Authorising once at the handshake and
                # never again would keep streaming a user's session events
                # long after they logged out on a shared machine.
                remaining = payload["exp"] - time.time()
                if remaining <= 0:
                    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                    break

                # receive(), not receive_text(): a browser can send a Blob,
                # and receive_text() raises KeyError on a frame carrying
                # "bytes" instead of "text", killing the handler on traffic
                # that is perfectly well formed.
                message = await asyncio.wait_for(
                    websocket.receive(), timeout=remaining
                )
                if message["type"] == "websocket.disconnect":
                    break
                # Nothing is expected from the client; anything it sends is
                # ignored. The read exists to notice the disconnect.
        except (WebSocketDisconnect, asyncio.TimeoutError):
            pass
        finally:
            await manager.disconnect(user.id, websocket)

    return app


app = create_app()
