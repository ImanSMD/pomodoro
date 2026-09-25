"""Per-user WebSocket fan-out.

State is in memory, which is correct for a single API container and wrong for
more than one: a second replica would hold its own registry and a user
connected to replica A would never see an event published on replica B. If
this is ever scaled out, the broadcast has to go through Redis pub/sub (or
Postgres LISTEN/NOTIFY) instead of this dict.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any
from uuid import UUID

from fastapi import WebSocket

logger = logging.getLogger("uvicorn.error")

# A send that has not completed in this long means the client is not reading.
# Broadcasting happens inline in the request handler after the commit, so
# without a deadline one backgrounded tab applying TCP backpressure would park
# POST /sessions/start forever — for a row that is already written.
SEND_TIMEOUT_SECONDS = 5.0


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[UUID, set[WebSocket]] = defaultdict(set)
        # One lock for the registry. Broadcasting iterates it while a
        # disconnecting socket may be mutating it, which raises
        # "Set changed size during iteration" under any real concurrency.
        self._lock = asyncio.Lock()

    async def connect(self, user_id: UUID, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections[user_id].add(websocket)

    async def disconnect(self, user_id: UUID, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections[user_id].discard(websocket)
            if not self._connections[user_id]:
                # Drop the empty set, or the dict grows one entry per user
                # who has ever connected and never shrinks.
                self._connections.pop(user_id, None)

    async def broadcast(self, user_id: UUID, event: str, payload: Any = None) -> None:
        async with self._lock:
            targets = list(self._connections.get(user_id, ()))

        message = {"event": event, "data": payload}
        for websocket in targets:
            try:
                await asyncio.wait_for(
                    websocket.send_json(message), timeout=SEND_TIMEOUT_SECONDS
                )
            except (Exception, asyncio.TimeoutError):
                # Dropped, or simply not reading. Either way the request that
                # triggered this must not suffer for it — the database write
                # has happened and the response is owed. The client recovers
                # by reconnecting and re-reading /sessions/active.
                logger.debug("dropping unresponsive websocket for user %s", user_id)
                await self.disconnect(user_id, websocket)

    def connection_count(self, user_id: UUID) -> int:
        return len(self._connections.get(user_id, ()))
