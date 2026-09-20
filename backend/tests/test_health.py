import httpx
import pytest
from asgi_lifespan import LifespanManager

from app.config import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_health_returns_ok(test_settings: Settings):
    # Built from test settings rather than the module-level app: that one is
    # wired to the real DATABASE_URL, and running its lifespan here would point
    # the suite at the development database the moment /api/health grows a
    # readiness query.
    app = create_app(test_settings)

    # LifespanManager, not a bare ASGITransport: the engine and session factory
    # hang off the app's lifespan, and a transport that never runs it would
    # exercise an app whose startup never happened.
    async with LifespanManager(app) as manager:
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
