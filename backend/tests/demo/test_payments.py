"""A demo lot is never payable, by anyone (B2 Q1): its sale settles as 'demo', with the winner
and price recorded as for any sale (one settlement per lot), and the payment path refuses it with
DEMO_NOT_PAYABLE before any gateway call, so no Razorpay order is ever created."""

from collections.abc import Awaitable, Callable

import asyncpg
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.closing import Closer, Outcome
from app.services.payments import PaymentService
from tests.closing.conftest import Ended
from tests.lifecycle.conftest import User, bearer
from tests.payments.fake_gateway import FakeGateway


async def test_a_person_who_wins_a_demo_lot_can_never_pay(
    api: AsyncClient,
    make_user: Callable[..., Awaitable[User]],
    ended: Ended,
    gateway: FakeGateway,
    payment_service: PaymentService,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    db: asyncpg.Connection,
) -> None:
    loser_id, _ = await make_user()
    winner_id, winner = await make_user()
    lot = await ended(bids=[(loser_id, 10_000), (winner_id, 10_500)])
    await db.execute("UPDATE auctions SET is_demo = true WHERE id = $1", lot)
    assert await Closer(redis, sessions).close(lot) is Outcome.SETTLED
    row = await db.fetchrow(
        "SELECT status, winner_id, final_price FROM settlements WHERE auction_id = $1", lot
    )
    assert tuple(row) == ("demo", winner_id, 10_500)
    assert await db.fetchval("SELECT status FROM auctions WHERE id = $1", lot) == "closed_sold"

    url = f"/api/v1/auctions/{lot}/payment"
    started = await api.post(url, headers=bearer(winner))
    confirmed = await api.post(
        f"{url}/confirm",
        json={
            "razorpay_order_id": "order_demo",
            "razorpay_payment_id": "pay_demo",
            "razorpay_signature": "0" * 64,
        },
        headers=bearer(winner),
    )
    for response in (started, confirmed):
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "DEMO_NOT_PAYABLE"
    assert gateway.calls == []  # no order, no lookup: nothing reached Razorpay
    payments = await db.fetchval(
        "SELECT count(*) FROM payments p JOIN settlements s ON s.id = p.settlement_id "
        "WHERE s.auction_id = $1",
        lot,
    )
    assert payments == 0
    settlement = await api.get(f"/api/v1/auctions/{lot}/settlement", headers=bearer(winner))
    assert settlement.json()["status"] == "demo"  # the winner still reads the outcome
