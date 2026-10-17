"""Demo bots (brief 12.3, 12.4): WebSocket clients that bid through the real path.

A bot is one demo account's socket on one API instance. It authenticates with a minted token
(services/demo_accounts.py), subscribes to the lot and bids the minimum it last saw, so bots on
both instances bidding at once produce real BID_TOO_LOW rejections. It paces itself far below the
per-user limit (bid_times), holds back while it leads, and keeps the acknowledgements it received:
the verification checks every one of them against the ledger (check 6). The watcher is the same
socket without bidding; it counts auction_closed frames.
"""

import asyncio
import json
import random
from typing import Any
from uuid import UUID, uuid4

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

REPLY_TIMEOUT_S = 5.0
REPLIES = ("bid_accepted", "bid_rejected", "error")


def bid_times(rng: random.Random, window_s: float, bids: int, min_gap_s: float) -> list[float]:
    """When one bot bids, in seconds from the start of bidding: one bid per equal slot of the
    window, jittered inside its slot so that two bids are never closer than min_gap_s and the
    bots do not move in step. At most as many bids as the gap allows (window / gap)."""
    count = min(bids, int(window_s // min_gap_s))
    if count <= 0:
        return []
    slot = window_s / count
    return [i * slot + rng.random() * (slot - min_gap_s) for i in range(count)]


class BotError(Exception):
    """The socket was refused (an error frame or a close) before it could bid."""


class BotSocket:
    def __init__(self, url: str, token: str, user_id: UUID, auction_id: UUID) -> None:
        self.url, self.user_id, self.auction_id = url, user_id, auction_id
        self._token = token
        self.min_next: int | None = None
        self._increment = 0
        self._last_seq = 0  # the newest sequence number this socket has seen
        self._mine = 0  # the newest sequence number of this bot's own accepted bids
        self.acks: list[tuple[int, str, int]] = []  # (sequence_no, bidder_id, amount)
        self.closed_events = 0  # auction_closed frames received
        self.close_code: int | None = None
        self._ws: ClientConnection | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._reader: asyncio.Task[None] | None = None

    @property
    def leading(self) -> bool:
        return self._mine > 0 and self._mine == self._last_seq

    @property
    def connected(self) -> bool:
        return self._reader is not None and self.close_code is None

    async def open(self) -> None:
        """Connect with no Origin header (a non-browser client), authenticate, subscribe."""
        self._ws = await connect(self.url, open_timeout=REPLY_TIMEOUT_S, max_size=2**20)
        await self._send({"type": "authenticate", "token": self._token})
        await self._until("authenticated")
        await self._send({"type": "subscribe", "auction_id": str(self.auction_id)})
        snapshot = (await self._until("subscribed"))["snapshot"]
        self._increment = int(snapshot["min_increment"])
        self.min_next = int(snapshot["min_next"])
        self._last_seq = int(snapshot["sequence_no"])
        if snapshot.get("you_lead"):
            self._mine = self._last_seq
        self._reader = asyncio.create_task(self._read())

    async def bid(self) -> str | None:
        """One bid at the minimum this bot last saw. None while it leads (it holds back);
        otherwise "accepted" or the rejection code ("CLOSED" or "TIMEOUT" without a reply)."""
        if self.leading or self.min_next is None:
            return None
        if not self.connected:
            return "CLOSED"
        amount, request_id = self.min_next, uuid4().hex
        reply: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = reply
        try:
            await self._send(
                {
                    "type": "place_bid",
                    "auction_id": str(self.auction_id),
                    "amount": amount,
                    "request_id": request_id,
                }
            )
            frame = await asyncio.wait_for(reply, REPLY_TIMEOUT_S)
        except ConnectionClosed:
            return "CLOSED"
        except TimeoutError:
            return "TIMEOUT"
        finally:
            self._pending.pop(request_id, None)
        if frame["type"] == "bid_accepted":
            sequence_no, accepted = int(frame["sequence_no"]), int(frame["amount"])
            self.acks.append((sequence_no, str(self.user_id), accepted))
            self._mine = max(self._mine, sequence_no)
            self._seen(sequence_no, accepted)
            return "accepted"
        if frame.get("min_next") is not None:  # BID_TOO_LOW says what the minimum is now
            self.min_next = max(self.min_next or 0, int(frame["min_next"]))
        return str(frame.get("code") or frame["type"])

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
        if self._reader is not None:
            await self._reader

    # ---------------------------------------------------------------- internals

    async def _send(self, message: dict[str, Any]) -> None:
        assert self._ws is not None
        await self._ws.send(json.dumps(message))

    async def _until(self, kind: str) -> dict[str, Any]:
        """Reads frames until one of `kind` (used before the reader starts)."""
        assert self._ws is not None
        try:
            async with asyncio.timeout(REPLY_TIMEOUT_S):
                while True:
                    frame = json.loads(await self._ws.recv())
                    if frame.get("type") == kind:
                        return frame
                    if frame.get("type") == "error":
                        raise BotError(frame.get("code"))
        except ConnectionClosed as exc:
            raise BotError(f"closed {exc.rcvd.code if exc.rcvd else None}") from exc

    def _seen(self, sequence_no: int, amount: int) -> None:
        if sequence_no > self._last_seq:
            self._last_seq = sequence_no
            self.min_next = amount + self._increment

    async def _read(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                frame = json.loads(raw)
                kind = frame.get("type")
                waiting = self._pending.get(frame.get("request_id") or "")
                if waiting is not None and kind in REPLIES and not waiting.done():
                    waiting.set_result(frame)
                elif kind == "bid_placed":
                    self._seen(int(frame["sequence_no"]), int(frame["amount"]))
                elif kind == "auction_closed":
                    self.closed_events += 1
        except ConnectionClosed:
            pass
        finally:
            self.close_code = self._ws.close_code or 1000
            for waiting in self._pending.values():
                if not waiting.done():
                    waiting.set_exception(ConnectionClosed(None, None))
