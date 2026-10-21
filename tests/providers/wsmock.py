"""A scripted local websocket server standing in for a vendor socket."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from websockets.asyncio.server import ServerConnection, serve


@dataclass
class Conn:
    ws: ServerConnection
    path: str
    headers: dict[str, str]
    received: list[Any] = field(default_factory=list)  # parsed JSON or raw bytes

    async def recv(self) -> Any:
        msg = await self.ws.recv()
        parsed = json.loads(msg) if isinstance(msg, str) else msg
        self.received.append(parsed)
        return parsed

    async def recv_until(self, pred: Callable[[Any], bool]) -> Any:
        while True:
            m = await self.recv()
            if pred(m):
                return m

    async def send(self, obj: Any) -> None:
        await self.ws.send(json.dumps(obj) if not isinstance(obj, str | bytes) else obj)


@dataclass
class Server:
    url: str
    conns: list[Conn] = field(default_factory=list)


Handler = Callable[[Conn], Awaitable[None]]


@asynccontextmanager
async def ws_server(handler: Handler) -> AsyncIterator[Server]:
    srv: Server | None = None

    async def _handle(ws: ServerConnection) -> None:
        assert srv is not None
        req = ws.request
        assert req is not None
        conn = Conn(
            ws=ws, path=req.path, headers={k.lower(): v for k, v in req.headers.raw_items()}
        )
        srv.conns.append(conn)
        await handler(conn)

    async with serve(_handle, "127.0.0.1", 0) as server:
        port = next(iter(server.sockets)).getsockname()[1]
        srv = Server(url=f"ws://127.0.0.1:{port}")
        yield srv
