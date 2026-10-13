"""Public live snapshot and the REST fallback for bids and maxima (FR-26, FR-33, DC-9, UC-01 2a).

The fallback calls the same BidService with the same request_id contract. The HTTP response is
the acknowledgement; room and user events are published after it is sent (FR-40).
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import (
    current_user,
    get_bid_service,
    get_limiter,
    get_redis,
    get_sessions,
    optional_user,
    require_roles,
)
from app.core.config import settings
from app.core.errors import AppError
from app.core.ratelimit import RateLimit, RateLimiter
from app.schemas.ws import REQUEST_ID, rejection_text
from app.services import closing, live_events
from app.services.auth import CurrentUser
from app.services.bidding import (
    MAX_AMOUNT,
    BidResult,
    BidService,
    ProxyResult,
    log_bid,
    log_proxy,
)
from app.services.snapshot import SnapshotCache, live_snapshot

router = APIRouter(prefix="/api/v1/auctions", tags=["auctions"])

STATUS = {
    "VALIDATION_ERROR": 422,
    "NOT_FOUND": 404,
    "SELLER_CANNOT_BID": 403,
    "SERVICE_UNAVAILABLE": 503,
}


class BidRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: int = Field(ge=1, le=MAX_AMOUNT, strict=True)
    request_id: str = Field(pattern=REQUEST_ID)


class ProxyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_amount: int = Field(ge=1, le=MAX_AMOUNT, strict=True)
    request_id: str = Field(pattern=REQUEST_ID)


def _rejection(code: str, min_next: int | None) -> AppError:
    text = rejection_text(code)
    if min_next is not None:
        text = f"{text} (minimum next bid {min_next})"
    return AppError(code, text, STATUS.get(code, 409))


@router.get("/{auction_id}/live")
async def live(
    auction_id: UUID,
    request: Request,
    response: Response,
    viewer: CurrentUser | None = Depends(optional_user),
    redis: Redis = Depends(get_redis),
    sessions: async_sessionmaker[AsyncSession] = Depends(get_sessions),
) -> dict[str, Any]:
    """Public snapshot from live state (FR-26); drafts only for their seller or an admin.

    Anonymous means no Authorization header: a presented token must be valid (optional_user
    answers 401 otherwise), so no request skips both the cache and the anonymous limit. Anonymous
    bodies come from the per-instance 1 s cache; authenticated ones are always read fresh and
    carry the viewer's own fields. server_time_ms is stamped per response, outside the cache."""
    if viewer is None:
        cache: SnapshotCache = request.app.state.snapshot_cache
        body = await cache.get(auction_id, lambda: live_snapshot(auction_id, None, redis, sessions))
        response.headers["Cache-Control"] = "public, max-age=1"
    else:
        body = await live_snapshot(auction_id, viewer, redis, sessions)
        response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Authorization"  # a cached anonymous body never answers a token
    return body | {"server_time_ms": live_events.server_time_ms()}


@router.post("/{auction_id}/bids")
async def place_bid(
    auction_id: UUID,
    body: BidRequest,
    background: BackgroundTasks,
    user: CurrentUser = Depends(require_roles("bidder")),
    bids: BidService = Depends(get_bid_service),
    redis: Redis = Depends(get_redis),
    limiter: RateLimiter = Depends(get_limiter),
) -> dict[str, Any]:
    # the same per-user limit as the WebSocket path (FR-44, NFR-22)
    try:
        await limiter.hit(
            "ws_place_bid", str(user.id), RateLimit.parse(settings.rate_limit_ws_place_bid)
        )
    except AppError as exc:  # refused before arbitration: still one FR-44 line
        log_bid(auction_id, user.id, body.amount, body.request_id, BidResult(False, exc.code))
        raise
    result = await bids.place_bid(auction_id, user.id, body.amount, body.request_id)
    if not result.accepted:
        raise _rejection(result.code or "SERVICE_UNAVAILABLE", result.min_next)
    background.add_task(
        live_events.publish_bid_events, redis, auction_id, user.id, result, bids.record_outbid
    )
    return _bid_ack(auction_id, body.request_id, result)


@router.post("/{auction_id}/proxy")
async def set_proxy(
    auction_id: UUID,
    body: ProxyRequest,
    background: BackgroundTasks,
    user: CurrentUser = Depends(require_roles("bidder")),
    bids: BidService = Depends(get_bid_service),
    redis: Redis = Depends(get_redis),
    limiter: RateLimiter = Depends(get_limiter),
) -> dict[str, Any]:
    try:
        await limiter.hit(
            "ws_set_proxy", str(user.id), RateLimit.parse(settings.rate_limit_ws_set_proxy)
        )
    except AppError as exc:
        log_proxy(auction_id, user.id, body.request_id, ProxyResult(False, exc.code))
        raise
    result = await bids.set_proxy(auction_id, user.id, body.max_amount, body.request_id)
    if not result.armed:
        raise _rejection(result.code or "SERVICE_UNAVAILABLE", result.min_next)
    background.add_task(
        live_events.publish_bid_events, redis, auction_id, user.id, result, bids.record_outbid
    )
    return _proxy_ack(auction_id, body.request_id, result)


def _bid_ack(auction_id: UUID, request_id: str, r: BidResult) -> dict[str, Any]:
    return live_events.event(
        "bid_accepted",
        request_id=request_id,
        auction_id=str(auction_id),
        sequence_no=r.sequence_no,
        amount=r.amount,
        end_time_ms=r.end_time_ms,
        extended=r.extended,
        extension_count=r.extension_count,
        placed_at_ms=r.placed_at_ms,
    )


def _proxy_ack(auction_id: UUID, request_id: str, r: ProxyResult) -> dict[str, Any]:
    return live_events.event(
        "proxy_armed",
        request_id=request_id,
        auction_id=str(auction_id),
        max_amount=r.max_amount,  # the caller's own maximum (FR-51)
        active=r.active,
    )


@router.get("/{auction_id}/settlement")
async def settlement(
    auction_id: UUID,
    viewer: CurrentUser = Depends(current_user),
    sessions: async_sessionmaker[AsyncSession] = Depends(get_sessions),
) -> dict[str, Any]:
    """The winner, the seller and administrators only (FR-64, BR-10)."""
    return await closing.settlement_view(sessions, viewer, auction_id)
