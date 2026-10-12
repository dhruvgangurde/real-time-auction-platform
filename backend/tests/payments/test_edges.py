"""Payment edge paths (FR-65 to FR-68): an adopted order that does not match, confirming before
starting, a refused capture, and a second capture on a paid settlement."""

import logging

import asyncpg
import pytest
from httpx import AsyncClient

from app.services.payment_gateway import GatewayOrder
from app.services.payments import PaymentService
from tests.payments.fake_gateway import FakeGateway
from tests.payments.support import confirm, start, state


def _alerts(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.fields["alert"] for r in caplog.records if "alert" in getattr(r, "fields", {})]


async def test_adopted_order_with_another_amount_is_refused(
    api: AsyncClient,
    sold,
    gateway: FakeGateway,
    payment_service: PaymentService,
    db: asyncpg.Connection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sale = await sold()
    wrong = GatewayOrder("order_wrong1", 999, "INR", str(sale.settlement_id), "created")
    gateway.orders[wrong.id] = wrong
    with caplog.at_level(logging.CRITICAL, logger="payments"):
        response = await api.post(sale.payment_url, headers=sale.winner)
    assert (response.status_code, response.json()["error"]["code"]) == (409, "PAYMENT_MISMATCH")
    assert _alerts(caplog) == ["PAYMENT_MISMATCH"]
    assert (
        await db.fetchval(
            "SELECT count(*) FROM payments WHERE settlement_id = $1", sale.settlement_id
        )
        == 0
    )


async def test_confirm_needs_a_started_payment_and_a_settlement(
    api: AsyncClient, sold, ended, make_user, gateway: FakeGateway, payment_service: PaymentService
) -> None:
    sale = await sold()
    body = {
        "razorpay_order_id": "order_x1",
        "razorpay_payment_id": "pay_x1",
        "razorpay_signature": "a" * 64,
    }
    response = await api.post(f"{sale.payment_url}/confirm", json=body, headers=sale.winner)
    assert (response.status_code, response.json()["error"]["code"]) == (404, "NOT_FOUND")
    seller_id, _ = await make_user("seller")
    closing = await ended(seller=seller_id)  # still closing: no settlement yet
    response = await api.post(f"/api/v1/auctions/{closing}/payment", headers=sale.winner)
    assert (response.status_code, response.json()["error"]["code"]) == (404, "NOT_FOUND")


async def test_refused_capture_leaves_it_pending(
    api: AsyncClient,
    sold,
    gateway: FakeGateway,
    payment_service: PaymentService,
    db: asyncpg.Connection,
) -> None:
    """An authorized payment the gateway will not capture (amount differs) stays unapplied."""
    sale = await sold()
    order_id = (await start(api, sale))["order_id"]
    paid = gateway.pay(order_id, "authorized", amount=10_000)
    response = await confirm(api, sale, gateway, order_id, paid)
    assert (response.status_code, response.json()) == (202, {"status": "pending"})
    assert (await state(db, sale))["settlement"] == "pending_payment"


async def test_second_capture_on_a_paid_settlement_is_alerted(
    api: AsyncClient,
    sold,
    gateway: FakeGateway,
    payment_service: PaymentService,
    db: asyncpg.Connection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A second successful charge is never applied twice; it raises PAYMENT_DUPLICATE_CHARGE
    for a manual refund (refunds are out of scope)."""
    sale = await sold()
    order_id = (await start(api, sale))["order_id"]
    first = gateway.pay(order_id)
    assert (await confirm(api, sale, gateway, order_id, first)).json() == {"status": "paid"}
    after = await state(db, sale)
    second = gateway.pay(order_id)
    raw, headers = gateway.webhook("payment.captured", second)
    with caplog.at_level(logging.CRITICAL, logger="payments"):
        response = await api.post("/api/v1/payments/razorpay/webhook", content=raw, headers=headers)
    assert response.status_code == 200
    assert _alerts(caplog) == ["PAYMENT_DUPLICATE_CHARGE"]
    assert await state(db, sale) == after
