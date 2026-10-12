"""The single WebSocket endpoint, /api/v1/ws (FR-28 to FR-34, FR-40, FR-44, CI-3 to CI-6).

Protocol handling only: bidding rules live in BidService, identity in AuthService, fan-out in
the Hub. Close codes are defined in app.schemas.ws.CloseCode (docs/architecture.md section 10).

1. A present Origin must be on the allowlist; the check runs before accept (HTTP 403).
2. The first message must be `authenticate` within the timeout (4001); a bad token closes 4002.
   Tokens are only ever read from that message, never from the URL (CI-4).
3. Each message is size checked, schema validated (DC-11) and rate limited per user across
   instances (NFR-22). Invalid or over-limit messages get an `error` and a strike; too many
   strikes close 4009.
4. place_bid / set_proxy re-run the denylist check (FR-6), require the bidder role and a room
   subscription (FR-34), call the service, queue the acknowledgement, and only then publish the
   room and user events (FR-40). The send queue is FIFO, so the submitter always sees its ack
   before its own bid_placed.
"""

import asyncio
import contextlib
import json
import logging
import time
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, WebSocket
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import (
    get_auth_service,
    get_bid_service,
    get_clock,
    get_limiter,
    get_redis,
    get_sessions,
)
from app.core.clock import Clock
from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import request_id_var
from app.core.ratelimit import RateLimit, RateLimiter
from app.schemas.ws import (
    CLIENT_MESSAGE,
    CLIENT_TYPES,
    Authenticate,
    CloseCode,
    Ping,
    PlaceBid,
    SetProxy,
    Subscribe,
    Unsubscribe,
)
from app.services import live_events
from app.services.auth import AuthService, CurrentUser
from app.services.bidding import BidResult, BidService, ProxyResult
from app.services.fanout import Connection, Hub
from app.services.live_events import alias, event
from app.services.snapshot import live_snapshot

router = APIRouter()
log = logging.getLogger("ws")

REVOKED = {"INVALID_TOKEN", "TOKEN_EXPIRED", "ACCOUNT_SUSPENDED"}
REJECTION_TEXT = {
    "BID_TOO_LOW": "bid is below the minimum next bid",
    "BID_ABOVE_LIMIT": "amount is more than 100 times the starting price",
    "SELF_OUTBID_NOT_ALLOWED": "you already hold the highest bid",
    "AUCTION_CLOSED": "auction is not open for bids",
    "AUCTION_NOT_STARTED": "auction has not started",
    "BELOW_CURRENT_PRICE": "maximum is below the minimum next bid",
    "PROXY_CANNOT_DECREASE": "a maximum can only be raised",
    "SERVICE_UNAVAILABLE": "bidding is temporarily unavailable; retry with the same request_id",
    "RATE_LIMITED": "too many submissions",
    "NOT_SUBSCRIBED": "subscribe to the auction first",
    "FORBIDDEN": "bidder role required",
    "NOT_FOUND": "auction not found",
    "VALIDATION_ERROR": "invalid request",
}


class _Reject(Exception):
    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code, self.reason = code, reason


def _fields(**kwargs: Any) -> dict[str, Any]:
    return {"fields": {k: v for k, v in kwargs.items() if v is not None}}


async def _close(websocket: WebSocket, code: int, reason: str) -> None:
    with contextlib.suppress(Exception):
        await asyncio.wait_for(websocket.close(code=code, reason=reason), 1.0)


async def _handshake(websocket: WebSocket, auth: AuthService) -> tuple[CurrentUser, str]:
    """First message must authenticate (FR-28, CI-4)."""
    try:
        raw = await asyncio.wait_for(websocket.receive(), settings.ws_auth_timeout_s)
    except TimeoutError as exc:
        raise _Reject(CloseCode.AUTH_REQUIRED, "authenticate within the timeout") from exc
    if raw["type"] == "websocket.disconnect":
        raise _Reject(CloseCode.NORMAL, "client left")
    try:
        message = CLIENT_MESSAGE.validate_json(raw.get("text") or "")
    except ValidationError as exc:
        raise _Reject(CloseCode.AUTH_REQUIRED, "first message must be authenticate") from exc
    if not isinstance(message, Authenticate):
        raise _Reject(CloseCode.AUTH_REQUIRED, "first message must be authenticate")
    try:
        return await auth.authenticate(message.token), message.token
    except AppError as exc:
        raise _Reject(CloseCode.AUTH_FAILED, exc.code) from exc


@router.websocket("/api/v1/ws")
async def live_socket(
    websocket: WebSocket,
    auth: AuthService = Depends(get_auth_service),
    bids: BidService = Depends(get_bid_service),
    redis: Redis = Depends(get_redis),
    sessions: async_sessionmaker[AsyncSession] = Depends(get_sessions),
    limiter: RateLimiter = Depends(get_limiter),
    clock: Clock = Depends(get_clock),
) -> None:
    origin = websocket.headers.get("origin")
    if origin is not None and origin not in settings.origin_allowlist:
        log.warning("ws origin refused", extra=_fields(origin=origin))
        await websocket.close(code=CloseCode.POLICY)  # before accept: HTTP 403
        return
    await websocket.accept()
    conn_id = request_id_var.get()  # correlation id for every log line of this connection
    log.info("ws connected", extra=_fields(conn_id=conn_id))

    try:
        user, token = await _handshake(websocket, auth)
    except _Reject as r:
        log.info("ws closed", extra=_fields(conn_id=conn_id, code=int(r.code), reason=r.reason))
        await _close(websocket, r.code, r.reason)
        return

    hub: Hub = websocket.app.state.hub
    conn = Connection(websocket, conn_id, user, token)
    if not await hub.register(conn):
        log.info("ws closed", extra=_fields(conn_id=conn_id, code=4010, user_id=str(user.id)))
        await _close(websocket, CloseCode.TOO_MANY_CONNECTIONS, "too many connections")
        return
    log.info("ws authenticated", extra=_fields(conn_id=conn_id, user_id=str(user.id)))
    conn.offer(event("authenticated", user_id=str(user.id), expires_at_ms=user.expires_at * 1000))

    session = LiveSession(conn, hub, auth, bids, redis, sessions, limiter, clock)
    tasks = [
        asyncio.create_task(session.read_loop(), name=f"ws-read-{conn_id}"),
        asyncio.create_task(conn.run_writer(), name=f"ws-write-{conn_id}"),
        asyncio.create_task(session.watch(), name=f"ws-watch-{conn_id}"),
        asyncio.create_task(conn.closing.wait(), name=f"ws-closing-{conn_id}"),
    ]
    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in tasks:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(BaseException):
            await task
    await hub.unregister(conn)
    if conn.close_code is not None:
        if conn.close_code != CloseCode.SLOW_CONSUMER:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(conn.drain(), 0.5)
        await _close(websocket, conn.close_code, conn.close_reason)
    log.info(
        "ws closed",
        extra=_fields(
            conn_id=conn_id,
            user_id=str(conn.user.id),
            code=conn.close_code or CloseCode.NORMAL,
            reason=conn.close_reason or "client closed",
        ),
    )


class LiveSession:
    """Message handling for one authenticated connection."""

    def __init__(
        self,
        conn: Connection,
        hub: Hub,
        auth: AuthService,
        bids: BidService,
        redis: Redis,
        sessions: async_sessionmaker[AsyncSession],
        limiter: RateLimiter,
        clock: Clock,
    ) -> None:
        self.conn, self.hub, self.auth, self.bids = conn, hub, auth, bids
        self.redis, self.sessions, self.limiter, self.clock = redis, sessions, limiter, clock

    # ---------------------------------------------------------------- loops

    async def read_loop(self) -> None:
        while True:
            raw = await self.conn.ws.receive()
            if raw["type"] == "websocket.disconnect":
                return
            self.conn.last_seen = time.monotonic()
            await self._handle_frame(raw.get("text"))

    async def watch(self) -> None:
        """Token expiry (4003) and idle timeout (4011)."""
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.conn.closing.wait(), settings.ws_watch_interval_s)
                return
            if self.clock.now().timestamp() >= self.conn.user.expires_at:
                self.conn.close(CloseCode.TOKEN_EXPIRED, "access token expired")
            elif time.monotonic() - self.conn.last_seen > settings.ws_idle_timeout_s:
                self.conn.close(CloseCode.IDLE_TIMEOUT, "idle")

    # ---------------------------------------------------------------- frames

    def _strike(self, code: str, message: str, request_id: str | None = None) -> None:
        self.conn.strikes += 1
        self.conn.offer(event("error", code=code, message=message, request_id=request_id))
        if self.conn.strikes >= settings.ws_max_strikes:
            self.conn.close(CloseCode.POLICY_VIOLATION, "too many invalid messages")

    async def _handle_frame(self, text: str | None) -> None:
        if text is None:
            return self._strike("VALIDATION_ERROR", "text frames only")
        if len(text.encode()) > settings.ws_max_message_bytes:
            return self._strike("MESSAGE_TOO_LARGE", "message exceeds the size limit")
        try:
            data = json.loads(text)
        except ValueError:
            return self._strike("VALIDATION_ERROR", "malformed JSON")
        if not isinstance(data, dict) or data.get("type") not in CLIENT_TYPES:
            return self._strike("UNKNOWN_TYPE", "unknown message type")
        request_id = data.get("request_id") if isinstance(data.get("request_id"), str) else None
        try:
            message = CLIENT_MESSAGE.validate_python(data)
        except ValidationError:
            return self._strike("VALIDATION_ERROR", "invalid message", request_id)
        log.info("ws message", extra=_fields(type=data["type"], bid_request_id=request_id))
        try:
            match message:
                case Ping():
                    self.conn.offer(event("pong"))
                case Authenticate():
                    await self._reauthenticate(message)
                case Subscribe():
                    await self._subscribe(message.auction_id)
                case Unsubscribe():
                    await self.hub.leave(self.conn, message.auction_id)
                    self.conn.offer(event("unsubscribed", auction_id=str(message.auction_id)))
                case PlaceBid() | SetProxy():
                    await self._bid(message)
        except AppError as exc:
            if exc.code == "RATE_LIMITED":
                return self._strike(exc.code, exc.message, request_id)
            self.conn.offer(
                event("error", code=exc.code, message=exc.message, request_id=request_id)
            )

    # ---------------------------------------------------------------- handlers

    async def _reauthenticate(self, message: Authenticate) -> None:
        """A fresh token extends the connection past the old token's expiry."""
        try:
            user = await self.auth.authenticate(message.token)
        except AppError as exc:
            self.conn.close(CloseCode.AUTH_FAILED, exc.code)
            return
        if user.id != self.conn.user.id:
            self.conn.close(CloseCode.AUTH_FAILED, "token belongs to another user")
            return
        self.conn.user, self.conn.token = user, message.token
        self.conn.offer(
            event("authenticated", user_id=str(user.id), expires_at_ms=user.expires_at * 1000)
        )

    async def _limit(self, kind: str) -> None:
        spec = getattr(settings, f"rate_limit_ws_{kind}")
        await self.limiter.hit(f"ws_{kind}", str(self.conn.user.id), RateLimit.parse(spec))

    async def _subscribe(self, auction_id: UUID) -> None:
        await self._limit("subscribe")
        if (
            auction_id not in self.conn.rooms
            and len(self.conn.rooms) >= settings.ws_max_subscriptions
        ):
            raise AppError("TOO_MANY_SUBSCRIPTIONS", "subscription limit reached", 400)
        # join before reading the snapshot: an event racing the read is delivered, and the
        # client drops anything at or below snapshot.sequence_no
        await self.hub.join(self.conn, auction_id)
        try:
            snapshot = await live_snapshot(auction_id, self.conn.user, self.redis, self.sessions)
        except AppError:
            await self.hub.leave(self.conn, auction_id)
            raise
        self.conn.offer(
            event(
                "subscribed",
                auction_id=str(auction_id),
                your_alias=alias(auction_id, self.conn.user.id),
                snapshot=snapshot,
            )
        )
        log.info("ws subscribed", extra=_fields(auction_id=str(auction_id)))

    async def _gate(self, message: PlaceBid | SetProxy) -> str | None:
        """Checks before arbitration (FR-6, FR-7, FR-34, FR-42, FR-44). Returns a code or None."""
        try:
            user = await self.auth.authenticate(self.conn.token)  # denylist re-check (FR-6)
        except AppError as exc:
            return exc.code
        self.conn.user = user
        if "bidder" not in user.roles:
            return "FORBIDDEN"
        if message.auction_id not in self.conn.rooms:
            return "NOT_SUBSCRIBED"
        if self.hub.degraded:
            return "SERVICE_UNAVAILABLE"
        try:
            await self._limit("place_bid" if isinstance(message, PlaceBid) else "set_proxy")
        except AppError as exc:
            self.conn.strikes += 1
            if self.conn.strikes >= settings.ws_max_strikes:
                self.conn.close(CloseCode.POLICY_VIOLATION, "too many rate-limited messages")
            return exc.code
        return None

    async def _bid(self, message: PlaceBid | SetProxy) -> None:
        is_bid = isinstance(message, PlaceBid)
        code = await self._gate(message)
        if code is not None:
            self._reject(message, code)
            if code in REVOKED:
                self.conn.close(CloseCode.SESSION_REVOKED, code)
            return
        result: BidResult | ProxyResult
        if is_bid:
            assert isinstance(message, PlaceBid)
            result = await self.bids.place_bid(
                message.auction_id, self.conn.user.id, message.amount, message.request_id
            )
            ok = result.accepted
        else:
            assert isinstance(message, SetProxy)
            result = await self.bids.set_proxy(
                message.auction_id, self.conn.user.id, message.max_amount, message.request_id
            )
            ok = result.armed
        if not ok:
            self._reject(message, result.code or "SERVICE_UNAVAILABLE", result)
            return
        self.conn.offer(self._ack(message, result))  # acknowledge first (FR-40, NFR-3)
        await live_events.publish_bid_events(
            self.redis, message.auction_id, self.conn.user.id, result
        )

    @staticmethod
    def _ack(message: PlaceBid | SetProxy, result: BidResult | ProxyResult) -> dict[str, Any]:
        if isinstance(result, BidResult):
            return event(
                "bid_accepted",
                request_id=message.request_id,
                auction_id=str(message.auction_id),
                sequence_no=result.sequence_no,
                amount=result.amount,
                end_time_ms=result.end_time_ms,
                extended=result.extended,
                extension_count=result.extension_count,
            )
        return event(
            "proxy_armed",
            request_id=message.request_id,
            auction_id=str(message.auction_id),
            max_amount=result.max_amount,  # the owner's own maximum, to the owner only (FR-51)
            active=result.active,
        )

    def _reject(
        self,
        message: PlaceBid | SetProxy,
        code: str,
        result: BidResult | ProxyResult | None = None,
    ) -> None:
        """Rejections go to the submitter only (FR-41)."""
        extra: dict[str, Any] = {}
        if isinstance(result, BidResult):
            extra = {"current_highest": result.current_highest, "min_next": result.min_next}
        elif isinstance(result, ProxyResult):
            extra = {"min_next": result.min_next}
        self.conn.offer(
            event(
                "bid_rejected" if isinstance(message, PlaceBid) else "proxy_rejected",
                request_id=message.request_id,
                auction_id=str(message.auction_id),
                code=code,
                message=REJECTION_TEXT.get(code, code.lower().replace("_", " ")),
                **{k: v for k, v in extra.items() if v is not None},
            )
        )
