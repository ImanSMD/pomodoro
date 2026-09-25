"""The WebSocket that keeps a second tab or device in step."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
from app.main import create_app


@pytest.fixture(scope="module", autouse=True)
async def _truncate_after_module(engine):
    """Clean up rows the sync TestClient tests commit for real.

    TestClient drives its own event loop, so those tests cannot use the
    rolled-back session fixture — their writes are permanent. This fixture
    runs on the pytest loop at module teardown, which does not clash.
    """
    yield
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE users CASCADE"))


@pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b.c"])
def test_a_bad_token_is_refused(test_settings: Settings, token: str):
    """Closed before accept, so no handshake completes for an unproven caller.

    Sync, because TestClient drives its own loop; nesting it inside an async
    test deadlocks and mixes asyncpg across two loops.
    """
    with TestClient(create_app(test_settings)) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/ws?token={token}"):
                pass


def test_a_missing_token_is_refused(test_settings: Settings):
    with TestClient(create_app(test_settings)) as client:
        with pytest.raises(Exception):
            with client.websocket_connect("/ws"):
                pass


def test_session_events_reach_another_tab(test_settings: Settings):
    """The point of the socket: a second tab learns without polling.

    Everything goes through one TestClient so the HTTP requests and the socket
    share an event loop and an app instance — the in-memory registry lives on
    the app, so a second instance would never see the broadcast.
    """
    app = create_app(test_settings)
    email = f"ws-{uuid.uuid4().hex[:8]}@example.com"

    with TestClient(app) as client:
        registered = client.post(
            "/api/auth/register",
            json={
                "email": email,
                "password": "correct-horse-battery",
                "display_name": "WS",
            },
        )
        assert registered.status_code == 201, registered.text
        token = registered.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        task = client.post("/api/tasks", json={"title": "Write"}, headers=headers)
        task_id = task.json()["id"]

        with client.websocket_connect(f"/ws?token={token}") as websocket:
            started = client.post(
                "/api/sessions/start", json={"task_id": task_id}, headers=headers
            )
            assert started.status_code == 201, started.text
            session_id = started.json()["id"]

            event = websocket.receive_json()
            assert event["event"] == "session.started"
            assert event["data"]["id"] == session_id

            completed = client.post(
                f"/api/sessions/{session_id}/complete", headers=headers
            )
            assert completed.status_code == 200

            event = websocket.receive_json()
            assert event["event"] == "session.completed"
            assert event["data"]["status"] == "completed"


def test_events_do_not_leak_to_another_user(test_settings: Settings):
    """Asserts positively, because the obvious version cannot fail.

    Closing the socket and checking nothing "arrived" proves nothing:
    WebSocketTestSession.close() does not inspect the receive queue, and
    Starlette only re-raises queued entries that are BaseException — a leaked
    event dict is neither. Such a test passes identically whether broadcast is
    keyed by user or a global fan-out.

    So the watcher triggers its own event afterwards and asserts the FIRST
    frame it receives is that one. A leaked frame would be queued ahead of it.
    """
    app = create_app(test_settings)
    suffix = uuid.uuid4().hex[:8]

    with TestClient(app) as client:
        def register(name: str) -> str:
            response = client.post(
                "/api/auth/register",
                json={
                    "email": f"{name}-{suffix}@example.com",
                    "password": "correct-horse-battery",
                    "display_name": name,
                },
            )
            assert response.status_code == 201, response.text
            return response.json()["access_token"]

        watcher_token = register("watcher")
        actor_token = register("actor")
        watcher_headers = {"Authorization": f"Bearer {watcher_token}"}
        actor_headers = {"Authorization": f"Bearer {actor_token}"}

        their_task = client.post(
            "/api/tasks", json={"title": "Theirs"}, headers=actor_headers
        ).json()["id"]
        my_task = client.post(
            "/api/tasks", json={"title": "Mine"}, headers=watcher_headers
        ).json()["id"]

        with client.websocket_connect(f"/ws?token={watcher_token}") as websocket:
            # Someone else's session first.
            assert (
                client.post(
                    "/api/sessions/start",
                    json={"task_id": their_task},
                    headers=actor_headers,
                ).status_code
                == 201
            )
            # Then the watcher's own.
            mine = client.post(
                "/api/sessions/start",
                json={"task_id": my_task},
                headers=watcher_headers,
            )
            assert mine.status_code == 201, mine.text

            first = websocket.receive_json()
            assert first["event"] == "session.started"
            # If the other user's event had leaked it would be queued first.
            assert first["data"]["id"] == mine.json()["id"]


def test_a_binary_frame_does_not_kill_the_connection(test_settings: Settings):
    """A browser can send a Blob at any time.

    receive_text() raises KeyError on a frame carrying "bytes", which escaped
    the handler as an unhandled exception and dropped the socket.
    """
    app = create_app(test_settings)
    suffix = uuid.uuid4().hex[:8]

    with TestClient(app) as client:
        registered = client.post(
            "/api/auth/register",
            json={
                "email": f"binary-{suffix}@example.com",
                "password": "correct-horse-battery",
                "display_name": "B",
            },
        )
        token = registered.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        task_id = client.post(
            "/api/tasks", json={"title": "Write"}, headers=headers
        ).json()["id"]

        with client.websocket_connect(f"/ws?token={token}") as websocket:
            websocket.send_bytes(b"\x00\x01\x02")

            # The socket must still be live and still delivering.
            started = client.post(
                "/api/sessions/start", json={"task_id": task_id}, headers=headers
            )
            assert started.status_code == 201, started.text
            assert websocket.receive_json()["event"] == "session.started"


def test_an_expired_token_cannot_open_a_socket(test_settings: Settings):
    """The handshake half of bounding the socket's life.

    The other half — closing a live socket when its token expires — is not
    covered here: the shortest token this API mints lasts a minute, and a test
    that sleeps that long is not worth its runtime. The loop computes its
    deadline from payload["exp"], so it is a one-line claim, not a mechanism.
    """
    from app.core.security import create_access_token

    app = create_app(test_settings)
    expired = create_access_token(uuid.uuid4(), test_settings.jwt_secret, ttl_minutes=-1)

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/ws?token={expired}"):
                pass
