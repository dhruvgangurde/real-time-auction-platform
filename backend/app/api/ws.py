"""The single WebSocket endpoint, /api/v1/ws (FR-28 to FR-34, FR-40, FR-44, CI-3 to CI-6).

Protocol handling only: bidding rules live in BidService, identity in AuthService, fan-out in
the Hub. Close codes are defined in app.schemas.ws.CloseCode (docs/architecture.md section 10).

1. A present Origin must be on the allowlist; the check runs before accept (HTTP 403).
2. The first message must be `authenticate` within the timeout (4001); a bad token closes 4002.
   Tokens are only ever read from that message, never from the URL (CI-4).
3. One socket per session (CI-3): a newer socket of the same session closes this one (4012).
   There is no idle timeout; dead peers are dropped by the protocol keepalive.
4. Every frame is counted against a per-connection cap in memory, validated (DC-11), then rate
   limited per user and type in Redis (NFR-22), bids most strictly. Refused frames earn strikes;
   one strike is forgiven per interval; too many close 4009.
5. place_bid / set_proxy and subscribe re-run the denylist check (FR-6). Bids then require the
   bidder role and a room subscription (FR-34), call the service, queue the acknowledgement, and
   only then publish the room and user events (FR-40). The send queue is FIFO, so the submitter
   always sees its ack before its own bid_placed.
6. An unexpected error in one message is logged and answered (a bid gets SERVICE_UNAVAILABLE so
   the retry contract holds); an unexpected error in a connection task closes 1011.
"""

import asyncio
import contextlib
import json
import logging
import time
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, WebSocket
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.websockets import WebSocketDisconnect, WebSocketDisconnected

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
    rejection_text,
)
from app.services import live_events
from app.services.auth import AuthService, CurrentUser
from app.services.bidding import BidResult, BidService, ProxyResult, log_bid, log_proxy
from app.services.fanout import Connection, Hub
from app.services.live_events import alias, event
from app.services.snapshot import live_snapshot

router = APIRouter()
log = logging.getLogger("ws")

# authentication failures that end the session, and the close code each one gets
CLOSE_FOR = {
    "INVALID_TOKEN": CloseCode.SESSION_REVOKED,
    "ACCOUNT_SUSPENDED": CloseCode.SESSION_REVOKED,
    "TOKEN_EXPIRED": CloseCode.TOKEN_EXPIRED,
}
# refusals that earn a strike; SERVICE_UNAVAILABLE never does (it is our fault, not theirs)
STRIKES = frozenset(
    {
        "VALIDATION_ERROR",
        "MESSAGE_TOO_LARGE",
        "UNKNOWN_TYPE",
        "RATE_LIMITED",
        "FORBIDDEN",
        "NOT_SUBSCRIBED",
    }
)
# with the limiter down these still run (bounded by the in-memory cap); bids and subscribe
# fail closed (FR-42, D11)
FAIL_OPEN = frozenset({"authenticate", "unsubscribe", "ping"})
# a connection task ending with one of these only means the client went away
DISCONNECTS = (WebSocketDisconnect, WebSocketDisconnected)

Bid = PlaceBid | SetProxy


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
    # server generated: presence and session bookkeeping never trust a client-chosen id; the
    # request id (X-Request-ID, possibly client supplied) stays in the log context only
    conn_id = uuid4().hex
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
        code, reason = CloseCode.TOO_MANY_SESSIONS, "too many sessions"
        log.info(
            "ws closed",
            extra=_fields(conn_id=conn_id, user_id=str(user.id), code=int(code), reason=reason),
        )
        await _close(websocket, code, reason)
        return
    log.info("ws authenticated", extra=_fields(conn_id=conn_id, user_id=str(user.id)))
    conn.offer(event("authenticated", user_id=str(user.id), expires_at_ms=user.expires_at * 1000))

    session = LiveSession(conn, hub, auth, bids, redis, sessions, limiter, clock)
    tasks = [
        asyncio.create_task(session.read_loop(), name="read"),
        asyncio.create_task(conn.run_writer(), name="write"),
        asyncio.create_task(session.watch(), name="watch"),
        asyncio.create_task(conn.closing.wait(), name="closing"),
    ]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            exc = None if task.cancelled() else task.exception()
            if exc is not None and not isinstance(exc, DISCONNECTS):
                log.error(
                    "ws connection task failed",
                    exc_info=exc,
                    extra=_fields(conn_id=conn_id, user_id=str(conn.user.id), task=task.get_name()),
                )
                conn.close(CloseCode.INTERNAL, "internal error")
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await hub.unregister(conn)

    if conn.close_code is not None:
        if conn.close_code != CloseCode.SLOW_CONSUMER:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(conn.drain(), 0.5)
        await _close(websocket, conn.close_code, conn.close_reason)
        code, reason = conn.close_code, conn.close_reason
    elif session.client_code is not None:
        code, reason = session.client_code, "client closed"
    else:
        code, reason = 1006, "connection lost"
    log.info(
        "ws closed",
        extra=_fields(conn_id=conn_id, user_id=str(conn.user.id), code=int(code), reason=reason),
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
        self.client_code: int | None = None  # close code the client sent, if it closed
        self.strikes = 0
        self.strike_mark = 0.0  # clock time from which the next strike is forgiven
        self.window_start = 0.0  # in-memory message cap window (monotonic)
        self.window_count = 0

    # ---------------------------------------------------------------- loops

    async def read_loop(self) -> None:
        while True:
            raw = await self.conn.ws.receive()
            if raw["type"] == "websocket.disconnect":
                self.client_code = raw.get("code")
                return
            await self._handle_frame(raw.get("text"))

    async def watch(self) -> None:
        """Token expiry (4003). No idle timeout: a quiet spectator is fine, and dead peers are
        dropped by the protocol keepalive (uvicorn ws ping 20 s, timeout 20 s)."""
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.conn.closing.wait(), settings.ws_watch_interval_s)
                return
            if self.clock.now().timestamp() >= self.conn.user.expires_at:
                self.conn.close(CloseCode.TOKEN_EXPIRED, "access token expired")

    # ---------------------------------------------------------------- strikes and caps

    def _strike(self) -> None:
        """One strike; one is forgiven per ws_strike_decay_s; too many close 4009."""
        now, decay = self.clock.now().timestamp(), settings.ws_strike_decay_s
        if self.strikes:
            forgiven = int((now - self.strike_mark) // decay)
            self.strikes = max(0, self.strikes - forgiven)
            self.strike_mark += forgiven * decay
        if not self.strikes:
            self.strike_mark = now
        self.strikes += 1
        if self.strikes >= settings.ws_max_strikes:
            self.conn.close(CloseCode.POLICY_VIOLATION, "too many refused messages")

    def _error(self, code: str, message: str, request_id: str | None = None) -> None:
        self.conn.offer(event("error", code=code, message=message, request_id=request_id))
        if code in STRIKES:
            self._strike()

    def _over_cap(self) -> bool:
        """Per-connection message cap, counted in memory for every frame (CI-3)."""
        now = time.monotonic()
        if now - self.window_start >= 1.0:
            self.window_start, self.window_count = now, 0
        self.window_count += 1
        return self.window_count > settings.ws_max_messages_per_s

    async def _limit(self, kind: str) -> AppError | None:
        """Per-user, per-type limit shared across instances (NFR-22). Returns the refusal."""
        spec = getattr(settings, f"rate_limit_ws_{kind}")
        try:
            await self.limiter.hit(f"ws_{kind}", str(self.conn.user.id), RateLimit.parse(spec))
        except AppError as exc:
            if exc.code == "SERVICE_UNAVAILABLE" and kind in FAIL_OPEN:
                return None
            return exc
        return None

    async def _revalidate(self) -> str | None:
        """FR-6: the token is still valid and unexpired, the session not revoked and the account
        still active. Returns the failure code."""
        try:
            self.conn.user = await self.auth.authenticate(self.conn.token)
        except AppError as exc:
            return exc.code
        return None

    # ---------------------------------------------------------------- frames

    async def _handle_frame(self, text: str | None) -> None:
        # counted before anything else, refused only once parsed, so a bid is answered as a bid
        over_cap = self._over_cap()
        if text is None:
            return self._error("VALIDATION_ERROR", "text frames only")
        if len(text.encode()) > settings.ws_max_message_bytes:
            return self._error("MESSAGE_TOO_LARGE", "message exceeds the size limit")
        try:
            data = json.loads(text)
        except ValueError:
            return self._error("VALIDATION_ERROR", "malformed JSON")
        if not isinstance(data, dict) or data.get("type") not in CLIENT_TYPES:
            return self._error("UNKNOWN_TYPE", "unknown message type")
        request_id = data.get("request_id") if isinstance(data.get("request_id"), str) else None
        try:
            message = CLIENT_MESSAGE.validate_python(data)
        except ValidationError:
            return self._error("VALIDATION_ERROR", "invalid message", request_id)
        log.info(
            "ws message",
            extra=_fields(conn_id=self.conn.id, type=data["type"], bid_request_id=request_id),
        )
        try:
            if isinstance(message, Bid):
                await self._bid(message, over_cap)
            elif over_cap:
                self._error("RATE_LIMITED", "too many messages", request_id)
            elif (refused := await self._limit(message.type)) is not None:
                self._error(refused.code, refused.message, request_id)
            else:
                await self._handle(message)
        except AppError as exc:
            self._error(exc.code, exc.message, request_id)
        except Exception:
            log.exception(
                "ws message failed",
                extra=_fields(conn_id=self.conn.id, type=data["type"], bid_request_id=request_id),
            )
            if isinstance(message, Bid):
                # outcome unknown to the client: it retries with the same request_id (FR-42)
                self._refuse(message, "SERVICE_UNAVAILABLE")
            else:
                self.conn.offer(
                    event(
                        "error",
                        code="INTERNAL_ERROR",
                        message="internal error",
                        request_id=request_id,
                    )
                )

    async def _handle(self, message: Authenticate | Subscribe | Unsubscribe | Ping) -> None:
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

    # ---------------------------------------------------------------- handlers

    async def _reauthenticate(self, message: Authenticate) -> None:
        """A refreshed token (same session) extends the connection past the old token's
        expiry. A token of another session needs its own socket (CI-3)."""
        try:
            user = await self.auth.authenticate(message.token)
        except AppError as exc:
            self.conn.close(CloseCode.AUTH_FAILED, exc.code)
            return
        if user.id != self.conn.user.id or user.sid != self.conn.user.sid:
            self.conn.close(CloseCode.AUTH_FAILED, "token belongs to another session")
            return
        self.conn.user, self.conn.token = user, message.token
        self.conn.offer(
            event("authenticated", user_id=str(user.id), expires_at_ms=user.expires_at * 1000)
        )

    async def _subscribe(self, auction_id: UUID) -> None:
        if (code := await self._revalidate()) is not None:  # FR-6
            self.conn.offer(event("error", code=code, message=rejection_text(code)))
            self.conn.close(CLOSE_FOR.get(code, CloseCode.SESSION_REVOKED), code)
            return
        if (
            auction_id not in self.conn.rooms
            and len(self.conn.rooms) >= settings.ws_max_subscriptions
        ):
            raise AppError("TOO_MANY_SUBSCRIPTIONS", "subscription limit reached", 400)
        # join before reading the snapshot: an event racing the read is delivered, and the
        # client applies only events above snapshot.sequence_no
        await self.hub.join(self.conn, auction_id)
        try:
            snapshot = await live_snapshot(auction_id, self.conn.user, self.redis, self.sessions)
        except Exception:
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
        log.info("ws subscribed", extra=_fields(conn_id=self.conn.id, auction_id=str(auction_id)))

    async def _gate(self, message: Bid, over_cap: bool) -> str | None:
        """Checks before arbitration, cheapest first (FR-6, FR-7, FR-34, FR-42, FR-44, CI-3):
        the in-memory cap, the per-type Redis limit, the denylist re-check, the bidder role,
        the room subscription, degraded mode. Returns a rejection code or None."""
        if over_cap:
            return "RATE_LIMITED"
        refused = await self._limit("place_bid" if isinstance(message, PlaceBid) else "set_proxy")
        if refused is not None:
            return refused.code
        if (code := await self._revalidate()) is not None:
            return code
        if "bidder" not in self.conn.user.roles:
            return "FORBIDDEN"
        if message.auction_id not in self.conn.rooms:
            return "NOT_SUBSCRIBED"
        if self.hub.degraded:
            return "SERVICE_UNAVAILABLE"
        return None

    async def _bid(self, message: Bid, over_cap: bool) -> None:
        code = await self._gate(message, over_cap)
        if code is not None:
            self._refuse(message, code)
            if code in CLOSE_FOR:
                self.conn.close(CLOSE_FOR[code], code)
            elif code in STRIKES:
                self._strike()
            return
        result: BidResult | ProxyResult
        if isinstance(message, PlaceBid):
            result = await self.bids.place_bid(
                message.auction_id, self.conn.user.id, message.amount, message.request_id
            )
            ok = result.accepted
        else:
            result = await self.bids.set_proxy(
                message.auction_id, self.conn.user.id, message.max_amount, message.request_id
            )
            ok = result.armed
        if not ok:
            self._reject(message, result.code or "SERVICE_UNAVAILABLE", result)
            return
        self.conn.offer(self._ack(message, result))  # acknowledge first (FR-40, NFR-3)
        try:
            await live_events.publish_bid_events(
                self.redis, message.auction_id, self.conn.user.id, result
            )
        except Exception:
            # already acknowledged, so never answered twice; clients recover via presence/live
            log.exception(
                "bid events not published",
                extra=_fields(conn_id=self.conn.id, auction_id=str(message.auction_id)),
            )

    def _refuse(self, message: Bid, code: str) -> None:
        """A rejection decided before or around arbitration: log the FR-44 line, then answer."""
        if isinstance(message, PlaceBid):
            log_bid(
                message.auction_id,
                self.conn.user.id,
                message.amount,
                message.request_id,
                BidResult(accepted=False, code=code),
            )
        else:
            log_proxy(
                message.auction_id,
                self.conn.user.id,
                message.request_id,
                ProxyResult(armed=False, code=code),
            )
        self._reject(message, code)

    @staticmethod
    def _ack(message: Bid, result: BidResult | ProxyResult) -> dict[str, Any]:
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
        self, message: Bid, code: str, result: BidResult | ProxyResult | None = None
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
                message=rejection_text(code),
                **{k: v for k, v in extra.items() if v is not None},
            )
        )
