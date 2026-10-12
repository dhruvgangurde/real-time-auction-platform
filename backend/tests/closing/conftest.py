"""Phase 8 fixtures: ended auctions with a ledger, maxima and live state, and a room listener.
A sweep looks at every auction, so the leftovers of earlier tests are moved out of the way
first (quiet, from the scheduler fixtures)."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any, Self
from uuid import UUID

import asyncpg
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.live_events import room_channel
from app.services.live_state import init_live_state
from tests.bidding.conftest import auction, new_user  # noqa: F401  shared fixtures
from tests.conftest import TEST_REDIS_URL
from tests.db.conftest import make_auction, make_user
from tests.scheduler.conftest import quiet  # noqa: F401  autouse

Ended = Callable[..., Awaitable[UUID]]


@pytest.fixture
def ended(
    db: asyncpg.Connection, redis: Redis, sessions: async_sessionmaker[AsyncSession]
) -> Ended:
    """An auction that has ended, in `status` (closing by default): bids as (bidder, amount) in
    sequence order, active maxima for `proxies`, and live state unless live_state=False."""

    async def make(
        *,
        bids: list[tuple[UUID, int]] | None = None,
        proxies: list[UUID] | None = None,
        reserve: int | None = None,
        status: str = "closing",
        end_s: float = -1,
        live_state: bool = True,
        seller: UUID | None = None,
    ) -> UUID:
        now = await db.fetchval("SELECT now()")
        end = now + timedelta(seconds=end_s)
        auction_id = await make_auction(
            db,
            seller_id=seller or await make_user(db),
            status=status,
            reserve_price=reserve,
            start_time=now - timedelta(hours=1),
            end_time=end,
            original_end_time=end,
        )
        for seq, (bidder, amount) in enumerate(bids or [], 1):
            await db.execute(
                "INSERT INTO bids (auction_id, bidder_id, amount, sequence_no, bid_type) "
                "VALUES ($1, $2, $3, $4, 'manual')",
                auction_id,
                bidder,
                amount,
                seq,
            )
        if bids:
            await db.execute(
                "UPDATE auctions SET current_price = $2, bid_count = $3 WHERE id = $1",
                auction_id,
                bids[-1][1],
                len(bids),
            )
        for arm_seq, bidder in enumerate(proxies or [], 1):
            await db.execute(
                "INSERT INTO proxy_bids (auction_id, bidder_id, max_amount, arm_seq) "
                "VALUES ($1, $2, 900000, $3)",
                auction_id,
                bidder,
                arm_seq,
            )
        if live_state:
            await init_live_state(auction_id, redis, sessions)
        return auction_id

    return make


class Room:
    """Records every event published to an auction's room channel (pub/sub ignores the db)."""

    def __init__(self, auction_id: UUID) -> None:
        self._channel = room_channel(auction_id)
        self._client = Redis.from_url(TEST_REDIS_URL, decode_responses=True)  # no read timeout
        self._pubsub = self._client.pubsub()
        self.events: list[dict[str, Any]] = []

    async def __aenter__(self) -> Self:
        await self._pubsub.subscribe(self._channel)
        while (await self._pubsub.get_message(timeout=1.0) or {}).get("type") != "subscribe":
            pass
        self._reader = asyncio.create_task(self._read())
        return self

    async def _read(self) -> None:
        while True:
            message = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=0.05)
            if message is not None:
                self.events.append(json.loads(message["data"]))

    async def __aexit__(self, *_: object) -> None:
        await asyncio.sleep(0.3)  # let in-flight messages arrive
        self._reader.cancel()
        await self._pubsub.aclose()
        await self._client.aclose()


async def settlement_rows(db: asyncpg.Connection, auction_id: UUID) -> list[asyncpg.Record]:
    return await db.fetch("SELECT * FROM settlements WHERE auction_id = $1", auction_id)


async def terminal_audits(db: asyncpg.Connection, auction_id: UUID) -> list[asyncpg.Record]:
    """The closing -> terminal transition rows of an auction."""
    return await db.fetch(
        "SELECT before, after, actor_id FROM audit_log WHERE entity_type = 'auction' "
        "AND entity_id = $1 AND action = 'status_changed' AND before->>'status' = 'closing'",
        str(auction_id),
    )


async def notifications(db: asyncpg.Connection, auction_id: UUID) -> list[tuple[UUID, str]]:
    rows = await db.fetch(
        "SELECT user_id, type FROM notifications WHERE auction_id = $1", auction_id
    )
    return sorted((r["user_id"], r["type"]) for r in rows)
