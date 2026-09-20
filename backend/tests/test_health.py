import httpx
import pytest
from asgi_lifespan import LifespanManager

from app.main import app


@pytest.mark.asyncio
async def test_health_returns_ok():
    # LifespanManager, not a bare ASGITransport: from 1.2 the engine and session
    # factory hang off the app's lifespan, and a transport that never runs it
    # would exercise an app whose startup never happened — passing against an
    # uninitialised engine rather than failing loudly.
    async with LifespanManager(app) as manager:
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
