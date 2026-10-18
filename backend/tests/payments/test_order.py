"""Starting a payment (FR-65, FR-66, DC-10, DP-1): only the winner, the settlement's amount,
one gateway order per settlement however many times or how concurrently it is asked for."""

import asyncio
import json
from uuid import uuid4

import asyncpg
import pytest
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.closing import Closer, Outcome
from app.services.payments import PaymentService
from tests.payments.fake_gateway import FakeGateway
from tests.payments.support import Sale, start


async def _payment_rows(db: asyncpg.Connection, sale: Sale) -> int:
    return await db.fetchval(
        "SELECT count(*) FROM payments WHERE settlement_id = $1", sale.settlement_id
    )


async def test_only_the_winner_may_start_payment(
    api: AsyncClient, sold, payment_service: PaymentService, make_user, ended, redis, sessions
) -> None:
    """FR-65, BR-11: the seller and other bidders get NOT_WINNER; anonymous callers 401."""
    sale = await sold()
    for headers in (sale.seller, sale.loser):
        response = await api.post(sale.payment_url, headers=headers)
        assert (response.status_code, response.json()["error"]["code"]) == (403, "NOT_WINNER")
    assert (await api.post(sale.payment_url)).status_code == 401
    missing = await api.post(f"/api/v1/auctions/{uuid4()}/payment", headers=sale.winner)
    assert missing.status_code == 404

    # a no-sale settlement has no winner: nobody can pay it
    seller_id, seller = await make_user("seller")
    no_sale = await ended(seller=seller_id)
    assert await Closer(redis, sessions).close(no_sale) is Outcome.SETTLED
    for headers in (sale.winner, {"Authorization": f"Bearer {seller}"}):
        response = await api.post(f"/api/v1/auctions/{no_sale}/payment", headers=headers)
        assert response.json()["error"]["code"] == "NOT_WINNER"


async def test_order_carries_the_settlement_amount_and_is_reused(
    api: AsyncClient,
    sold,
    gateway: FakeGateway,
    payment_service: PaymentService,
    db: asyncpg.Connection,
) -> None:
    """FR-66: the amount comes from the settlement; a repeat returns the same order without a
    second gateway call; nothing the client sends can set the amount (DC-10, DC-11)."""
    sale = await sold()
    first = await start(api, sale)
    assert first["amount"] == 10_500 and first["currency"] == "INR"
    assert first["key_id"] == "rzp_test_fake" and first["status"] == "created"
    order = gateway.orders[first["order_id"]]
    assert (order.amount, order.receipt) == (10_500, str(sale.settlement_id))

    assert await start(api, sale) == first
    assert gateway.calls.count("create_order") == 1
    tampered = await api.post(sale.payment_url, json={"amount": 1}, headers=sale.winner)
    assert (tampered.status_code, tampered.json()["error"]["code"]) == (422, "VALIDATION_ERROR")
    empty = await api.post(sale.payment_url, json={}, headers=sale.winner)  # DC-11: {} is no input
    assert empty.status_code == 200 and empty.json()["amount"] == 10_500
    assert gateway.calls.count("create_order") == 1

    row = await db.fetchrow(
        "SELECT actor_id, after FROM audit_log WHERE entity_type = 'payment' "
        "AND action = 'payment_created' AND entity_id = $1",
        first["payment_id"],
    )
    assert row["actor_id"] == sale.winner_id
    assert json.loads(row["after"])["order_id"] == first["order_id"]


@pytest.mark.parametrize("unique_receipts", [True, False])
async def test_concurrent_starts_share_one_order(
    sold,
    gateway: FakeGateway,
    payment_service: PaymentService,
    db: asyncpg.Connection,
    sessions: async_sessionmaker[AsyncSession],
    unique_receipts: bool,
) -> None:
    """FR-66, DC-5: ten starts at once give one payment row and one order id, whether or not
    the gateway itself refuses a duplicate receipt."""
    from app.services.auth import CurrentUser

    gateway.unique_receipts = unique_receipts
    sale = await sold()
    user = CurrentUser(sale.winner_id, ("bidder",), uuid4(), 0)
    views = await asyncio.gather(*(payment_service.start(user, sale.auction_id) for _ in range(10)))
    assert len({v["order_id"] for v in views}) == 1
    assert await _payment_rows(db, sale) == 1


async def test_an_order_whose_commit_failed_is_adopted(
    api: AsyncClient, sold, gateway: FakeGateway, payment_service: PaymentService
) -> None:
    """FR-66: an order created at the gateway by an attempt that never committed is found by
    its receipt and used, not refused for ever."""
    sale = await sold()
    orphan = await gateway.create_order(
        amount=10_500, currency="INR", receipt=str(sale.settlement_id), notes={}
    )
    view = await start(api, sale)
    assert view["order_id"] == orphan.id
    assert "find_order" in gateway.calls


async def test_gateway_down_stores_nothing_and_blocks_nothing_else(
    api: AsyncClient,
    sold,
    gateway: FakeGateway,
    payment_service: PaymentService,
    db: asyncpg.Connection,
) -> None:
    """DP-1: a gateway outage refuses payment with 503 and writes nothing; closing and
    settlement creation (in `sold`) never touch the gateway."""
    gateway.down = True
    sale = await sold()  # closed and settled with the gateway down
    response = await api.post(sale.payment_url, headers=sale.winner)
    assert (response.status_code, response.json()["error"]["code"]) == (
        503,
        "SERVICE_UNAVAILABLE",
    )
    assert await _payment_rows(db, sale) == 0
    gateway.down = False
    assert (await start(api, sale))["status"] == "created"


async def test_paid_and_too_small_settlements_are_refused(
    api: AsyncClient, sold, gateway: FakeGateway, payment_service: PaymentService, db
) -> None:
    """FR-65: ALREADY_PAID once paid; below the gateway's 100 paise minimum it is refused
    before any gateway call."""
    sale = await sold()
    await start(api, sale)
    await db.execute(
        "UPDATE settlements SET status = 'paid', paid_at = now() WHERE id = $1",
        sale.settlement_id,
    )
    response = await api.post(sale.payment_url, headers=sale.winner)
    assert (response.status_code, response.json()["error"]["code"]) == (409, "ALREADY_PAID")

    tiny = await sold(amounts=(40, 50))
    calls = len(gateway.calls)
    response = await api.post(tiny.payment_url, headers=tiny.winner)
    assert (response.status_code, response.json()["error"]["code"]) == (
        409,
        "AMOUNT_BELOW_GATEWAY_MINIMUM",
    )
    assert len(gateway.calls) == calls


async def test_gateway_rejecting_the_order_is_unavailable(
    api: AsyncClient,
    sold,
    gateway: FakeGateway,
    payment_service: PaymentService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.payment_gateway import GatewayRejected

    sale = await sold()

    async def refuse(**_: object) -> None:
        raise GatewayRejected("Authentication failed")

    monkeypatch.setattr(gateway, "create_order", refuse)
    response = await api.post(sale.payment_url, headers=sale.winner)
    assert response.status_code == 503


async def test_redis_for_limits_only(redis: Redis) -> None:
    """The payment path never stores anything in Redis but rate-limit counters (DC-1)."""
    assert await redis.dbsize() == 0


async def test_insert_ignores_a_conflict_on_any_unique_key(
    sold, sessions, db: asyncpg.Connection
) -> None:
    """FR-66, DC-5: the losing concurrent start may meet the winner's row on gateway_order_id
    before it meets it on idempotency_key; that is 'already there', never an error."""
    from app.repositories import PaymentRepository

    first, second = await sold(), await sold()
    async with sessions() as session, session.begin():
        repo = PaymentRepository(session)
        assert await repo.insert_ignore(
            settlement_id=first.settlement_id,
            amount=10_500,
            status="created",
            gateway_order_id="order_sharedRace1",
            idempotency_key=f"order:{first.settlement_id}",
        )
    async with sessions() as session, session.begin():
        assert not await PaymentRepository(session).insert_ignore(
            settlement_id=second.settlement_id,
            amount=10_500,
            status="created",
            gateway_order_id="order_sharedRace1",  # same order, another key
            idempotency_key=f"order:{second.settlement_id}",
        )
    assert (
        await db.fetchval(
            "SELECT count(*) FROM payments WHERE gateway_order_id = 'order_sharedRace1'"
        )
        == 1
    )
