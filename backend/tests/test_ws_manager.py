"""ConnectionManager: the per-user registry behind the socket."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from app.ws.manager import ConnectionManager

pytestmark = pytest.mark.asyncio


class FakeSocket:
    def __init__(self, fail: bool = False, close_fails: bool = False) -> None:
        self.sent: list[dict] = []
        self.fail = fail
        self.close_fails = close_fails
        self.closed_with: int | None = None

    async def send_json(self, message: dict) -> None:
        if self.fail:
            raise RuntimeError("client is gone")
        self.sent.append(message)

    async def close(self, code: int = 1000) -> None:
        if self.close_fails:
            raise RuntimeError("socket is past closing")
        self.closed_with = code


async def test_broadcast_reaches_every_socket_for_that_user():
    manager = ConnectionManager()
    user_id = uuid4()
    first, second = FakeSocket(), FakeSocket()

    await manager.connect(user_id, first)
    await manager.connect(user_id, second)
    await manager.broadcast(user_id, "session.started", {"id": "abc"})

    assert first.sent == second.sent == [
        {"event": "session.started", "data": {"id": "abc"}}
    ]


async def test_broadcast_does_not_reach_another_user():
    manager = ConnectionManager()
    mine, theirs = uuid4(), uuid4()
    watcher = FakeSocket()

    await manager.connect(theirs, watcher)
    await manager.broadcast(mine, "session.started")

    assert watcher.sent == []


async def test_a_dead_socket_is_dropped_and_does_not_break_the_broadcast():
    """The write has already happened; a dead client must not fail the request."""
    manager = ConnectionManager()
    user_id = uuid4()
    dead, alive = FakeSocket(fail=True), FakeSocket()

    await manager.connect(user_id, dead)
    await manager.connect(user_id, alive)
    await manager.broadcast(user_id, "session.started")

    assert len(alive.sent) == 1
    assert manager.connection_count(user_id) == 1


async def test_disconnecting_the_last_socket_frees_the_entry():
    # Otherwise the dict grows one entry per user who has ever connected.
    manager = ConnectionManager()
    user_id = uuid4()
    socket = FakeSocket()

    await manager.connect(user_id, socket)
    await manager.disconnect(user_id, socket)

    assert manager.connection_count(user_id) == 0
    assert user_id not in manager._connections


async def test_concurrent_connects_and_broadcasts_do_not_race():
    """Broadcast iterates the registry while sockets may be joining or leaving.

    Without the lock this raises "Set changed size during iteration".
    """
    manager = ConnectionManager()
    user_id = uuid4()
    sockets = [FakeSocket() for _ in range(50)]

    await asyncio.gather(
        *(manager.connect(user_id, s) for s in sockets),
        *(manager.broadcast(user_id, "tick") for _ in range(20)),
        *(manager.disconnect(user_id, s) for s in sockets[:25]),
    )


async def test_a_silent_socket_does_not_block_the_broadcast():
    """A client that simply stops reading must not park the request.

    The broadcast runs inline in the handler after the commit, so without a
    deadline one backgrounded tab applying TCP backpressure would hold
    POST /sessions/start open forever — for a row already written.
    """
    import app.ws.manager as manager_module

    class SilentSocket:
        def __init__(self) -> None:
            self.sent: list[dict] = []

        async def send_json(self, message: dict) -> None:
            await asyncio.sleep(3600)

    manager = ConnectionManager()
    user_id = uuid4()
    silent, alive = SilentSocket(), FakeSocket()
    await manager.connect(user_id, silent)
    await manager.connect(user_id, alive)

    original = manager_module.SEND_TIMEOUT_SECONDS
    manager_module.SEND_TIMEOUT_SECONDS = 0.05
    try:
        await asyncio.wait_for(
            manager.broadcast(user_id, "session.started"), timeout=5
        )
    finally:
        manager_module.SEND_TIMEOUT_SECONDS = original

    # The responsive socket still got it, and the silent one was dropped.
    assert len(alive.sent) == 1
    assert manager.connection_count(user_id) == 1


async def test_a_dropped_socket_is_closed_and_not_merely_deregistered():
    """Deregistering alone leaves the client believing it is subscribed.

    The handler in main.py is parked in receive(), so removing the socket from
    the registry does not end the connection. The client reconnects from
    onclose and has no heartbeat, so without the close it never learns it has
    stopped receiving events — it shows a live socket and a frozen timer until
    the access token expires, up to ACCESS_TOKEN_TTL_MINUTES later.
    """
    manager = ConnectionManager()
    user_id = uuid4()
    dead = FakeSocket(fail=True)

    await manager.connect(user_id, dead)
    await manager.broadcast(user_id, "session.started")

    assert dead.closed_with is not None, "dropped without being closed"
    assert manager.connection_count(user_id) == 0


async def test_a_socket_that_cannot_be_closed_is_still_forgotten():
    # Closing is best-effort: the write has committed and the response is owed,
    # so a socket too broken to close must not fail the request either.
    manager = ConnectionManager()
    user_id = uuid4()
    dead, alive = FakeSocket(fail=True, close_fails=True), FakeSocket()

    await manager.connect(user_id, dead)
    await manager.connect(user_id, alive)
    await manager.broadcast(user_id, "session.started")

    assert manager.connection_count(user_id) == 1
    assert len(alive.sent) == 1
