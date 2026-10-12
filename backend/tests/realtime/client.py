"""Test WebSocket client and the documented client-side ordering rule."""

import asyncio
import base64
import json
import os
from collections.abc import Callable
from typing import Any

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

ORIGIN = "http://localhost:5173"


class Client:
    """Reads every frame in the background; tests wait for frames matching a predicate."""

    def __init__(self, ws: ClientConnection) -> None:
        self.ws = ws
        self.frames: list[dict[str, Any]] = []
        self.raw: list[str] = []
        self._taken: set[int] = set()
        self.close_code: int | None = None
        self._reader = asyncio.create_task(self._read())

    @classmethod
    async def open(
        cls,
        url: str,
        token: str | None = None,
        origin: str | None = ORIGIN,
        headers: dict[str, str] | None = None,
    ) -> "Client":
        ws = await connect(
            url,
            origin=origin,  # type: ignore[arg-type]
            additional_headers=headers,
            open_timeout=5,
            max_size=2**20,
        )
        client = cls(ws)
        if token is not None:
            await client.send({"type": "authenticate", "token": token})
            await client.expect("authenticated")
        return client

    async def _read(self) -> None:
        try:
            async for raw in self.ws:
                text = raw if isinstance(raw, str) else raw.decode()
                self.raw.append(text)
                self.frames.append(json.loads(text))
        except ConnectionClosed:
            pass
        self.close_code = self.ws.close_code

    async def send(self, message: dict[str, Any] | str) -> None:
        await self.ws.send(message if isinstance(message, str) else json.dumps(message))

    async def wait(
        self, pred: Callable[[dict[str, Any]], bool], timeout: float = 5.0
    ) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            for i, frame in enumerate(self.frames):
                if i not in self._taken and pred(frame):
                    self._taken.add(i)
                    return frame
            if self._reader.done():
                raise AssertionError(f"closed ({self.close_code}) while waiting; got {self.frames}")
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError(f"timed out waiting; got {self.frames}")
            await asyncio.sleep(0.005)

    async def expect(self, kind: str, timeout: float = 5.0, **match: Any) -> dict[str, Any]:
        return await self.wait(
            lambda f: f.get("type") == kind and all(f.get(k) == v for k, v in match.items()),
            timeout,
        )

    async def closed(self, timeout: float = 5.0) -> int | None:
        await asyncio.wait_for(asyncio.shield(self._reader), timeout)
        return self.close_code

    def of_type(self, kind: str) -> list[dict[str, Any]]:
        return [f for f in self.frames if f.get("type") == kind]

    async def subscribe(self, auction_id: Any) -> dict[str, Any]:
        await self.send({"type": "subscribe", "auction_id": str(auction_id)})
        return await self.expect("subscribed", auction_id=str(auction_id))

    async def close(self) -> None:
        await self.ws.close()
        self._reader.cancel()


async def open_silent(
    host: str, port: int, token: str
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """A half-open peer: handshake and authenticate by hand, then never read again and never
    answer a ping, like a client whose network vanished without a close."""
    reader, writer = await asyncio.open_connection(host, port)
    key = base64.b64encode(os.urandom(16)).decode()
    writer.write(
        (
            f"GET /api/v1/ws HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
            f"Origin: {ORIGIN}\r\n\r\n"
        ).encode()
    )
    head = await reader.readuntil(b"\r\n\r\n")
    assert b" 101 " in head.split(b"\r\n")[0], head
    payload = json.dumps({"type": "authenticate", "token": token}).encode()
    mask, n = os.urandom(4), len(payload)
    length = bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + n.to_bytes(2, "big")
    writer.write(
        bytes([0x81]) + length + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    )
    first = await asyncio.wait_for(reader.read(4096), 5)
    assert b'"authenticated"' in first, first
    return reader, writer


def reorder(events: list[dict[str, Any]], last_applied: int = 0) -> list[dict[str, Any]]:
    """The client ordering rule (architecture.md): apply room events in sequence order and drop
    anything at or below the last applied sequence (duplicates from retries, late arrivals).
    A gap means a lost event: refetch /live."""
    out: list[dict[str, Any]] = []
    for e in sorted(events, key=lambda e: e["sequence_no"]):
        if e["sequence_no"] > last_applied:
            out.append(e)
            last_applied = e["sequence_no"]
    return out
