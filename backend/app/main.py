from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import Settings, get_settings
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

    return app


app = create_app()
