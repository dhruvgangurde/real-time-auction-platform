"""Checkout callback (FR-67, FR-68, NFR-25, DC-5): the gateway's signature binds the payment to
our stored order and is checked first; the outcome is read from the gateway and applied once."""

import logging

import asyncpg
import pytest
from httpx import AsyncClient

from app.services.live_events import user_channel
from app.services.payments import PaymentService
from tests.closing.conftest import Room
from tests.payments.fake_gateway import FakeGateway
from tests.payments.support import Sale, confirm, start, state


def _alerts(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.fields["alert"] for r in caplog.records if "alert" in getattr(r, "fields", {})]


async def _ready(api: AsyncClient, sold, gateway: FakeGateway) -> tuple[Sale, str]:
    sale = await sold()
    return sale, (await start(api, sale))["order_id"]


async def test_bad_signatures_change_nothing(
    api: AsyncClient,
    sold,
    gateway: FakeGateway,
    payment_service: PaymentService,
    db: asyncpg.Connection,
) -> None:
    """FR-67: a wrong signature, a signature for another order, or another settlement's order
    is INVALID_SIGNATURE with no state change; a malformed body never reaches the service."""
    sale, order_id = await _ready(api, sold, gateway)
    other, other_order = await _ready(api, sold, gateway)
    paid = gateway.pay(order_id)
    before = await state(db, sale)
    url = f"{sale.payment_url}/confirm"
    for order, payment, signature in (
        (order_id, paid, "0" * 64),  # forged
        (order_id, paid, gateway.checkout_signature(other_order, paid)),  # other order's
        (other_order, paid, gateway.checkout_signature(other_order, paid)),  # not our order
    ):
        body = {
            "razorpay_order_id": order,
            "razorpay_payment_id": payment,
            "razorpay_signature": signature,
        }
        response = await api.post(url, json=body, headers=sale.winner)
        assert (response.status_code, response.json()["error"]["code"]) == (
            400,
            "INVALID_SIGNATURE",
        )
    malformed = await api.post(
        url,
        json={
            "razorpay_order_id": order_id,
            "razorpay_payment_id": paid,
            "razorpay_signature": "not-hex",
            "card_number": "4111111111111111",
        },
        headers=sale.winner,
    )
    assert malformed.status_code == 422  # DC-10, DC-11: no card fields, no loose signatures
    loser = await api.post(
        url,
        json={
            "razorpay_order_id": order_id,
            "razorpay_payment_id": paid,
            "razorpay_signature": gateway.checkout_signature(order_id, paid),
        },
        headers=sale.loser,
    )
    assert loser.json()["error"]["code"] == "NOT_WINNER"
    assert await state(db, sale) == before
    assert (await state(db, other))["settlement"] == "pending_payment"


async def test_captured_payment_settles_once(
    api: AsyncClient,
    sold,
    gateway: FakeGateway,
    payment_service: PaymentService,
    db: asyncpg.Connection,
) -> None:
    """FR-68: paid, payment succeeded, audit rows for payment and settlement (FR-64), both
    parties notified, live push to the user channel (FR-71); a repeat changes nothing."""
    sale, order_id = await _ready(api, sold, gateway)
    paid = gateway.pay(order_id)
    async with Room(channel=user_channel(sale.winner_id)) as winner_channel:
        response = await confirm(api, sale, gateway, order_id, paid)
    assert (response.status_code, response.json()) == (200, {"status": "paid"})
    after = await state(db, sale)
    assert after == {
        "settlement": "paid",
        "paid_at_set": True,
        "payment": "succeeded",
        "gateway_payment_id": paid,
        "events": 1,
        "payment_audits": ["payment_succeeded"],
        "settlement_audits": 1,
        "notifications": sorted(
            [("payment_received", sale.winner_id), ("payment_received", sale.seller_id)]
        ),
    }
    pushed = [e for e in winner_channel.events if e["type"] == "notification"]
    assert [e["notification_type"] for e in pushed] == ["payment_received"]

    again = await confirm(api, sale, gateway, order_id, paid)
    assert (again.status_code, again.json()) == (200, {"status": "paid"})
    assert await state(db, sale) == after
    settlement = await api.get(
        f"/api/v1/auctions/{sale.auction_id}/settlement", headers=sale.seller
    )
    assert settlement.json()["status"] == "paid"
    assert settlement.json()["payment"]["status"] == "succeeded"  # FR-69


async def test_authorized_payment_is_captured_then_settled(
    api: AsyncClient, sold, gateway: FakeGateway, payment_service: PaymentService, db
) -> None:
    """The platform captures an authorized payment itself, so it never depends on the
    gateway's auto-capture setting; one capture call."""
    sale, order_id = await _ready(api, sold, gateway)
    paid = gateway.pay(order_id, "authorized")
    response = await confirm(api, sale, gateway, order_id, paid)
    assert response.json() == {"status": "paid"}
    assert gateway.payments[paid].status == "captured"
    assert gateway.calls.count("capture") == 1


async def test_capture_racing_auto_capture_is_fine(
    api: AsyncClient,
    sold,
    gateway: FakeGateway,
    payment_service: PaymentService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gateway captured it between our read and our capture: 'already captured' means
    read again, not failure."""
    sale, order_id = await _ready(api, sold, gateway)
    paid = gateway.pay(order_id, "authorized")
    capture = gateway.capture

    async def auto_captured_first(payment_id: str, amount: int, currency: str):
        gateway.set_status(payment_id, "captured")
        return await capture(payment_id, amount, currency)

    monkeypatch.setattr(gateway, "capture", auto_captured_first)
    assert (await confirm(api, sale, gateway, order_id, paid)).json() == {"status": "paid"}


async def test_gateway_unreachable_leaves_it_pending(
    api: AsyncClient, sold, gateway: FakeGateway, payment_service: PaymentService, db
) -> None:
    """A valid callback while the gateway cannot be read: 202 pending, nothing changed; the
    reconciler finishes it later (FR-69)."""
    sale, order_id = await _ready(api, sold, gateway)
    paid = gateway.pay(order_id)
    before = await state(db, sale)
    gateway.down = True
    response = await confirm(api, sale, gateway, order_id, paid)
    assert (response.status_code, response.json()) == (202, {"status": "pending"})
    assert await state(db, sale) == before


async def test_amount_mismatch_is_refused_and_alerted(
    api: AsyncClient,
    sold,
    gateway: FakeGateway,
    payment_service: PaymentService,
    db: asyncpg.Connection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sale, order_id = await _ready(api, sold, gateway)
    paid = gateway.pay(order_id, amount=9_999)
    before = await state(db, sale)
    with caplog.at_level(logging.CRITICAL, logger="payments"):
        response = await confirm(api, sale, gateway, order_id, paid)
    assert (response.status_code, response.json()["error"]["code"]) == (409, "PAYMENT_MISMATCH")
    assert _alerts(caplog) == ["PAYMENT_MISMATCH"]
    assert await state(db, sale) == before


async def test_failure_then_retry_on_the_same_order(
    api: AsyncClient,
    sold,
    gateway: FakeGateway,
    payment_service: PaymentService,
    db: asyncpg.Connection,
) -> None:
    """FR-68, B.5: a failed attempt marks the payment failed, keeps the settlement payable and
    tells the bidder; a later success on the same order settles it (failed -> succeeded)."""
    sale, order_id = await _ready(api, sold, gateway)
    failed = gateway.pay(order_id, "failed")
    response = await confirm(api, sale, gateway, order_id, failed)
    assert response.json() == {"status": "failed"}
    after_failure = await state(db, sale)
    assert after_failure["settlement"] == "pending_payment"
    assert after_failure["payment"] == "failed"
    assert after_failure["notifications"] == [("payment_failed", sale.winner_id)]
    assert (await start(api, sale))["order_id"] == order_id  # retry on the same order

    paid = gateway.pay(order_id)
    assert (await confirm(api, sale, gateway, order_id, paid)).json() == {"status": "paid"}
    final = await state(db, sale)
    assert (final["settlement"], final["payment"]) == ("paid", "succeeded")
    assert final["payment_audits"] == ["payment_failed", "payment_succeeded"]
    assert final["events"] == 2
