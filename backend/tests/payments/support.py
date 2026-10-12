"""Phase 9 payment fixtures and helpers: a sold auction with a pending_payment settlement, the
in-memory gateway, and the payment service wired to both (no network)."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg
import pytest
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.ratelimit import RateLimiter
from app.services.closing import Closer, Outcome
from app.services.payments import PaymentService
from tests.payments.fake_gateway import FakeGateway

KEY_SECRET = "test-key-secret-for-checkout"
WEBHOOK_SECRET = "test-webhook-secret"
WEBHOOK = "/api/v1/payments/razorpay/webhook"


@pytest.fixture
def gateway() -> FakeGateway:
    return FakeGateway(KEY_SECRET, WEBHOOK_SECRET)


@pytest.fixture
def payment_service(
    sessions: async_sessionmaker[AsyncSession],
    redis: Redis,
    gateway: FakeGateway,
    limiter: RateLimiter,
) -> PaymentService:
    return PaymentService(
        sessions, redis, gateway, limiter, key_secret=KEY_SECRET, webhook_secret=WEBHOOK_SECRET
    )


@dataclass
class Sale:
    auction_id: UUID
    settlement_id: UUID
    winner_id: UUID
    winner: dict[str, str]  # bearer headers
    seller_id: UUID
    seller: dict[str, str]
    loser_id: UUID
    loser: dict[str, str]

    @property
    def payment_url(self) -> str:
        return f"/api/v1/auctions/{self.auction_id}/payment"


@pytest.fixture
def sold(
    make_user: Any,
    ended: Any,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    db: asyncpg.Connection,
) -> Callable[..., Awaitable[Sale]]:
    """A closed_sold auction with its settlement in pending_payment (FR-60), final price
    10,500 unless `amounts` says otherwise; the last bidder wins."""

    async def make(amounts: tuple[int, int] = (10_000, 10_500)) -> Sale:
        seller_id, seller = await make_user("seller")
        loser_id, loser = await make_user()
        winner_id, winner = await make_user()
        auction_id = await ended(
            bids=[(loser_id, amounts[0]), (winner_id, amounts[1])], seller=seller_id
        )
        assert await Closer(redis, sessions).close(auction_id) is Outcome.SETTLED
        settlement_id = await db.fetchval(
            "SELECT id FROM settlements WHERE auction_id = $1", auction_id
        )
        return Sale(
            auction_id,
            settlement_id,
            winner_id,
            {"Authorization": f"Bearer {winner}"},
            seller_id,
            {"Authorization": f"Bearer {seller}"},
            loser_id,
            {"Authorization": f"Bearer {loser}"},
        )

    return make


async def start(api: AsyncClient, sale: Sale) -> dict[str, Any]:
    response = await api.post(sale.payment_url, headers=sale.winner)
    assert response.status_code == 200, response.text
    return response.json()


async def confirm(
    api: AsyncClient, sale: Sale, gateway: FakeGateway, order_id: str, payment_id: str
) -> Any:
    return await api.post(
        f"{sale.payment_url}/confirm",
        json={
            "razorpay_order_id": order_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature": gateway.checkout_signature(order_id, payment_id),
        },
        headers=sale.winner,
    )


async def state(db: asyncpg.Connection, sale: Sale) -> dict[str, Any]:
    """Everything a confirmation can change, for before/after comparisons."""
    settlement = await db.fetchrow(
        "SELECT status, paid_at FROM settlements WHERE id = $1", sale.settlement_id
    )
    payment = await db.fetchrow(
        "SELECT id, status, gateway_payment_id FROM payments WHERE settlement_id = $1",
        sale.settlement_id,
    )
    payment_id = payment["id"] if payment else None
    return {
        "settlement": settlement["status"],
        "paid_at_set": settlement["paid_at"] is not None,
        "payment": payment["status"] if payment else None,
        "gateway_payment_id": payment["gateway_payment_id"] if payment else None,
        "events": await db.fetchval(
            "SELECT count(*) FROM payment_events WHERE payment_id = $1", payment_id
        ),
        "payment_audits": [
            r["action"]
            for r in await db.fetch(
                "SELECT action FROM audit_log WHERE entity_type = 'payment' AND entity_id = $1 "
                "AND action <> 'payment_created' ORDER BY id",
                str(payment_id),
            )
        ],
        "settlement_audits": await db.fetchval(
            "SELECT count(*) FROM audit_log WHERE entity_type = 'settlement' AND entity_id = $1 "
            "AND action = 'status_changed'",
            str(sale.settlement_id),
        ),
        "notifications": sorted(
            (r["type"], r["user_id"])
            for r in await db.fetch(
                "SELECT type, user_id FROM notifications WHERE auction_id = $1 "
                "AND type IN ('payment_received', 'payment_failed')",
                sale.auction_id,
            )
        ),
    }
