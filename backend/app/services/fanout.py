"""Per-instance fan-out of Redis pub/sub events to local WebSocket connections
(FR-29, FR-31, FR-32, CI-3, CI-7, NFR-5, NFR-13).

One `Hub` per API instance holds one pub/sub connection. It subscribes to a room channel only
while a local socket is in that room and to a user channel only while that user has a local
socket (reference counted), and dispatches each message to the matching local connections.

One socket per session (CI-3): registering claims the session's slot with a new generation
(ws_claim.lua). Older sockets of the same session are closed with SESSION_REPLACED, locally at
once and on other instances through `session_replaced` on the user channel; the higher
generation always wins. A user holds at most ws_max_sessions_per_user sessions.

Each `Connection` has a bounded send queue drained by its own writer task. `offer()` never
blocks: a full queue closes that socket with SLOW_CONSUMER, so one slow client never stalls the
hub or anyone else.

If Redis is lost, the hub tells every local socket `system_status {degraded: true}`, keeps
retrying with backoff, resubscribes every reference-counted channel (including any referenced
while the resubscribe was in flight), and only then sends `degraded: false` (clients refetch
/live on recovery). While degraded, the WebSocket handler rejects bids with SERVICE_UNAVAILABLE.

Presence (FR-32): every interval the hub refreshes its connections in each room's presence set
and sends `presence {participants, sequence_no, end_time_ms}` to its local sockets in that
room. The sequence lets a client in a quiet room notice a lost bid_placed within one interval.
"""

import asyncio
import contextlib
import json
import logging
import time
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from redis.asyncio.client import PubSub
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from starlette.websockets import WebSocket

from app import lua
from app.core.config import settings
from app.schemas.ws import CloseCode
from app.services.auth import CurrentUser
from app.services.live_events import event, room_channel, user_channel
from app.services.live_state import auction_id_from_key, state_key

log = logging.getLogger("fanout")

CONN_SEQ_KEY = "ws:conn_seq"  # global generation counter for session ownership


def presence_key(auction_id: UUID) -> str:
    return f"auction:{{{auction_id}}}:presence"


def user_sessions_key(user_id: UUID) -> str:
    return f"ws:user:{user_id}:sessions"


def session_owner_key(sid: UUID) -> str:
    return f"ws:sid:{sid}"


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _presence_ttl_ms() -> int:
    return int(settings.ws_presence_interval_s * 3000) + 1000


class Connection:
    """One authenticated socket: identity, rooms and a bounded outbound queue."""

    def __init__(self, websocket: WebSocket, conn_id: str, user: CurrentUser, token: str) -> None:
        self.ws = websocket
        self.id = conn_id  # server generated, never taken from the client
        self.user = user
        self.token = token
        self.gen = 0  # session ownership generation; 0 when Redis was unreachable at register
        self.rooms: set[UUID] = set()
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(settings.ws_send_queue_size)
        self.closing = asyncio.Event()
        self.close_code: int | None = None
        self.close_reason = ""

    def offer(self, message: dict[str, Any]) -> bool:
        """Queue a message without ever blocking the caller (NFR-5)."""
        if self.closing.is_set():
            return False
        try:
            self.queue.put_nowait(message)
            return True
        except asyncio.QueueFull:
            self.close(CloseCode.SLOW_CONSUMER, "send queue overflow")
            return False

    def close(self, code: int, reason: str) -> None:
        if not self.closing.is_set():
            self.close_code, self.close_reason = code, reason
            self.closing.set()

    async def send_frame(self, message: dict[str, Any]) -> None:
        await self.ws.send_text(json.dumps(message, separators=(",", ":")))

    async def run_writer(self) -> None:
        while True:
            await self.send_frame(await self.queue.get())

    async def drain(self) -> None:
        """Flush what is already queued (e.g. a rejection before a policy close)."""
        while not self.queue.empty():
            await self.send_frame(self.queue.get_nowait())


class Hub:
    def __init__(self, redis_url: str, redis: Redis) -> None:
        self._url = redis_url
        self._redis = redis  # commands: presence and session ownership
        self._claim = redis.register_script(lua.source("ws_claim"))
        self._release = redis.register_script(lua.source("ws_release"))
        self._client: Redis | None = None
        self._pubsub: PubSub | None = None
        self._refs: dict[str, int] = {}
        self._subscribed: set[str] = set()  # channels the current pub/sub connection holds
        self._rooms: dict[UUID, set[Connection]] = {}
        self._users: dict[UUID, set[Connection]] = {}
        self._conns: set[Connection] = set()
        self._tasks: list[asyncio.Task[None]] = []
        self.degraded = False

    # ---------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        if not await self._reconnect():
            self._set_degraded(True)
        self._tasks = [
            asyncio.create_task(self._reader(), name="hub-reader"),
            asyncio.create_task(self._presence_loop(), name="hub-presence"),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self._close_pubsub()
        await self._redis.aclose()

    # ---------------------------------------------------------------- connections

    async def register(self, conn: Connection) -> bool:
        """Track a new socket and make it its session's only socket (CI-3). False when the
        user already holds the maximum number of other sessions."""
        user_id, sid = conn.user.id, conn.user.sid
        cap = settings.ws_max_sessions_per_user
        try:
            gen = int(
                await self._claim(
                    keys=[user_sessions_key(user_id), session_owner_key(sid), CONN_SEQ_KEY],
                    args=[str(sid), _now_ms(), _presence_ttl_ms(), cap],
                )
            )
            if not gen:
                return False
            conn.gen = gen
        except RedisError:
            # best effort: the socket works, and generation 0 loses to any later socket
            self._set_degraded(True)
        self._replace(user_id, str(sid), conn.gen)  # older sockets of this session here
        self._conns.add(conn)
        self._users.setdefault(user_id, set()).add(conn)
        await self._ref(user_channel(user_id))
        if conn.gen:
            message = event("session_replaced", sid=str(sid), gen=conn.gen)
            with contextlib.suppress(RedisError):  # at-most-once, like session_revoked
                await self._redis.publish(user_channel(user_id), json.dumps(message))
        if self.degraded:
            conn.offer(event("system_status", degraded=True))
        return True

    async def unregister(self, conn: Connection) -> None:
        for auction_id in list(conn.rooms):
            await self.leave(conn, auction_id)
        self._conns.discard(conn)
        peers = self._users.get(conn.user.id)
        if peers is not None:
            peers.discard(conn)
            if not peers:
                del self._users[conn.user.id]
        await self._unref(user_channel(conn.user.id))
        if conn.gen:
            with contextlib.suppress(RedisError):
                await self._release(
                    keys=[user_sessions_key(conn.user.id), session_owner_key(conn.user.sid)],
                    args=[str(conn.user.sid), conn.gen],
                )

    def _replace(self, user_id: UUID, sid: str, gen: int) -> None:
        """Close local sockets of this session that are older than generation `gen`."""
        for conn in list(self._users.get(user_id, ())):
            if str(conn.user.sid) == sid and conn.gen < gen:
                conn.close(CloseCode.SESSION_REPLACED, "replaced by a newer connection")

    async def join(self, conn: Connection, auction_id: UUID) -> None:
        conn.rooms.add(auction_id)
        self._rooms.setdefault(auction_id, set()).add(conn)
        await self._ref(room_channel(auction_id))
        with contextlib.suppress(RedisError):
            await self._redis.zadd(
                presence_key(auction_id), {conn.id: _now_ms() + _presence_ttl_ms()}
            )

    async def leave(self, conn: Connection, auction_id: UUID) -> None:
        conn.rooms.discard(auction_id)
        members = self._rooms.get(auction_id)
        if members is not None:
            members.discard(conn)
            if not members:
                del self._rooms[auction_id]
        await self._unref(room_channel(auction_id))
        with contextlib.suppress(RedisError):
            await self._redis.zrem(presence_key(auction_id), conn.id)

    # ---------------------------------------------------------------- subscriptions

    async def _ref(self, channel: str) -> None:
        self._refs[channel] = self._refs.get(channel, 0) + 1
        if self._refs[channel] == 1 and not self.degraded:
            try:
                await self._ensure_pubsub().subscribe(channel)
                self._subscribed.add(channel)
            except (RedisError, OSError):
                self._set_degraded(True)  # the reconnect resubscribes every referenced channel

    async def _unref(self, channel: str) -> None:
        count = self._refs.get(channel, 0) - 1
        if count > 0:
            self._refs[channel] = count
            return
        self._refs.pop(channel, None)
        self._subscribed.discard(channel)
        if self._pubsub is not None and not self.degraded:
            with contextlib.suppress(RedisError, OSError):
                await self._pubsub.unsubscribe(channel)

    def _ensure_pubsub(self) -> PubSub:
        if self._pubsub is None:
            # no transparent retries: a dropped connection must surface so we can go degraded
            self._client = Redis.from_url(
                self._url,
                decode_responses=True,
                socket_connect_timeout=1.0,
                retry=Retry(NoBackoff(), 0),
            )
            self._pubsub = self._client.pubsub()
        return self._pubsub

    async def _close_pubsub(self) -> None:
        pubsub, client = self._pubsub, self._client
        self._pubsub, self._client = None, None
        self._subscribed = set()
        if pubsub is not None:
            with contextlib.suppress(Exception):
                await pubsub.aclose()
        if client is not None:
            with contextlib.suppress(Exception):
                await client.aclose()

    async def _reconnect(self) -> bool:
        """Fresh pub/sub connection with every referenced channel resubscribed, then healthy."""
        await self._close_pubsub()
        try:
            pubsub = self._ensure_pubsub()
            assert self._client is not None
            await self._client.ping()
            # a socket may join a room while SUBSCRIBE is in flight (its _ref skips subscribing
            # while degraded): repeat until nothing referenced is missing, then report recovery
            # with no await in between
            while missing := set(self._refs) - self._subscribed:
                await pubsub.subscribe(*missing)
                self._subscribed |= missing
        except (RedisError, OSError):
            await self._close_pubsub()
            return False
        self._set_degraded(False)
        return True

    def _set_degraded(self, value: bool) -> None:
        if self.degraded == value:
            return
        self.degraded = value
        log.warning("hub degraded" if value else "hub recovered", extra={"fields": {}})
        message = event("system_status", degraded=value)
        for conn in list(self._conns):
            conn.offer(message)

    # ---------------------------------------------------------------- reading and dispatch

    async def _reader(self) -> None:
        backoff = 0.1
        while True:
            if self.degraded:
                if await self._reconnect():
                    backoff = 0.1
                else:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 2.0)
                continue
            pubsub = self._pubsub
            if pubsub is None or not self._refs or not pubsub.subscribed:
                await asyncio.sleep(0.02)
                continue
            try:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
            except (RedisError, OSError):
                self._set_degraded(True)
                continue
            if message is not None:
                self._dispatch(message["channel"], message["data"])

    def _dispatch(self, channel: str, data: str) -> None:
        try:
            message = json.loads(data)
        except (TypeError, ValueError):
            return
        if channel.startswith("auction:"):
            for conn in list(self._rooms.get(auction_id_from_key(channel), ())):
                conn.offer(message)
            return
        if channel.startswith("user:"):
            user_id = UUID(channel.split(":", 2)[1])
            kind = message.get("type")
            if kind == "session_replaced":
                self._replace(user_id, str(message.get("sid")), int(message.get("gen") or 0))
                return
            for conn in list(self._users.get(user_id, ())):
                if kind == "session_revoked":
                    sid = message.get("sid")
                    if sid is None or sid == str(conn.user.sid):
                        conn.close(CloseCode.SESSION_REVOKED, "session revoked")
                else:
                    conn.offer(message)

    @property
    def socket_count(self) -> int:
        """Authenticated sockets on this instance (NFR-8)."""
        return len(self._conns)

    # ---------------------------------------------------------------- presence (FR-32)

    async def _presence_loop(self) -> None:
        while True:
            await asyncio.sleep(settings.ws_presence_interval_s)
            try:
                await self.presence_tick()
            except (RedisError, OSError):
                self._set_degraded(True)

    async def presence_tick(self) -> None:
        rooms = {a: set(c) for a, c in self._rooms.items() if c}
        if not rooms and not self._conns:
            return
        now, ttl = _now_ms(), _presence_ttl_ms()
        async with self._redis.pipeline(transaction=False) as pipe:
            for auction_id, conns in rooms.items():
                key = presence_key(auction_id)
                pipe.zremrangebyscore(key, "-inf", now)
                pipe.zadd(key, {c.id: now + ttl for c in conns})
                pipe.pexpire(key, ttl)
                pipe.zcard(key)
                pipe.hmget(state_key(auction_id), "sequence", "end_time_ms")
            for conn in self._conns:  # keep this instance's sessions counted (CI-3)
                pipe.zadd(user_sessions_key(conn.user.id), {str(conn.user.sid): now + ttl})
                pipe.pexpire(user_sessions_key(conn.user.id), ttl)
                pipe.pexpire(session_owner_key(conn.user.sid), ttl)
            results = await pipe.execute()
        for i, (auction_id, conns) in enumerate(rooms.items()):
            participants, (sequence, end_ms) = results[i * 5 + 3], results[i * 5 + 4]
            message = event(
                "presence",
                auction_id=str(auction_id),
                participants=participants,
                sequence_no=int(sequence) if sequence is not None else None,
                end_time_ms=int(end_ms) if end_ms is not None else None,
            )
            for conn in conns:
                conn.offer(message)
