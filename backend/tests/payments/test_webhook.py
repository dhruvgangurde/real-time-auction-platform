"""Gateway webhook (FR-67, FR-68, NFR-25, DC-5): unauthenticated but admitted only by the
signature over the raw body, verified before parsing; every event applied at most once,
duplicates and out-of-order deliveries harmless."""

import json

import asyncpg
import pytest
from httpx import AsyncClient
from sqlalchemy.exc import OperationalError

from app.repositories import PaymentRepository
from app.services.payments import PaymentService
from tests.payments.fake_gateway import FakeGateway
from tests.payments.support import WEBHOOK, Sale, start, state


async def _ready(api: AsyncClient, sold) -> tuple[Sale, str]:
    sale = await sold()
    return sale, (await start(api, sale))["order_id"]


async def _post(api: AsyncClient, raw: bytes, headers: dict[str, str]):
    return await api.post(WEBHOOK, content=raw, headers=headers)


async def test_unsigned_or_tampered_webhooks_change_nothing(
    api: AsyncClient,
    sold,
    gateway: FakeGateway,
    payment_service: PaymentService,
    db: asyncpg.Connection,
) -> None:
    """FR-67, NFR-25: missing signature, wrong secret, one changed byte, or garbage: 400
    INVALID_SIGNATURE and not one row written."""
    sale, order_id = await _ready(api, sold)
    paid = gateway.pay(order_id)
    raw, headers = gateway.webhook("payment.captured", paid)
    before = await state(db, sale)
    unsigned = {k: v for k, v in headers.items() if k != "X-Razorpay-Signature"}
    tampered = raw.replace(b'"amount":10500', b'"amount":10501')
    for body, hdrs in (
        (raw, unsigned),
        (raw, headers | {"X-Razorpay-Signature": "f" * 64}),
        (tampered, headers),
        (b"not json at all", headers),
        (b"", unsigned),
    ):
        response = await _post(api, body, hdrs)
        assert (response.status_code, response.json()["error"]["code"]) == (
            400,
            "INVALID_SIGNATURE",
        )
    assert await state(db, sale) == before


async def test_oversized_body_is_refused_unread(
    api: AsyncClient, gateway: FakeGateway, payment_service: PaymentService
) -> None:
    raw = b"{" + b" " * 70_000 + b"}"
    response = await _post(api, raw, gateway.sign_webhook(raw))
    assert (response.status_code, response.json()["error"]["code"]) == (413, "PAYLOAD_TOO_LARGE")


async def test_captured_webhook_settles_once_and_redelivery_is_neutral(
    api: AsyncClient,
    sold,
    gateway: FakeGateway,
    payment_service: PaymentService,
    db: asyncpg.Connection,
) -> None:
    """FR-67, FR-68: settled from the signed payload with no outbound call; the same event
    again, or another event for the same payment, changes nothing."""
    sale, order_id = await _ready(api, sold)
    paid = gateway.pay(order_id)
    calls = len(gateway.calls)
    raw, headers = gateway.webhook("payment.captured", paid, event_id="evt_first")
    assert (await _post(api, raw, headers)).json() == {"status": "ok"}
    assert len(gateway.calls) == calls  # trusted the signed payload
    after = await state(db, sale)
    assert (after["settlement"], after["payment"], after["events"]) == ("paid", "succeeded", 1)

    assert (await _post(api, raw, headers)).status_code == 200  # redelivery
    raw2, headers2 = gateway.webhook("payment.captured", paid, event_id="evt_second")
    assert (await _post(api, raw2, headers2)).status_code == 200
    assert await state(db, sale) == after
    keys = await db.fetch("SELECT dedupe_key FROM payment_events")
    assert "webhook:evt_first" in {k["dedupe_key"] for k in keys}
    audit_text = json.dumps(
        [
            r["after"]
            for r in await db.fetch(
                "SELECT after FROM audit_log WHERE entity_type IN ('payment', 'settlement') "
                "AND after::text LIKE $1",
                f"%{paid}%",
            )
        ]
    )
    assert "payer@example.com" not in audit_text and "+9199" not in audit_text  # no PII


async def test_out_of_order_deliveries(
    api: AsyncClient,
    sold,
    gateway: FakeGateway,
    payment_service: PaymentService,
    db: asyncpg.Connection,
) -> None:
    """A failure arriving after the capture never undoes it; a payment that failed and was
    later authorised and captured (late authorisation) settles."""
    sale, order_id = await _ready(api, sold)
    paid = gateway.pay(order_id)
    for event in ("payment.captured", "payment.failed"):
        await _post(api, *gateway.webhook(event, paid))
    after = await state(db, sale)
    assert (after["settlement"], after["payment"]) == ("paid", "succeeded")
    assert after["notifications"].count(("payment_failed", sale.winner_id)) == 0

    late, late_order = await _ready(api, sold)
    payment = gateway.pay(late_order, "failed")
    await _post(api, *gateway.webhook("payment.failed", payment))
    assert (await state(db, late))["payment"] == "failed"
    gateway.set_status(payment, "captured")
    await _post(api, *gateway.webhook("payment.captured", payment))
    final = await state(db, late)
    assert (final["settlement"], final["payment"]) == ("paid", "succeeded")


async def test_ignored_deliveries_answer_200(
    api: AsyncClient,
    sold,
    gateway: FakeGateway,
    payment_service: PaymentService,
    db: asyncpg.Connection,
) -> None:
    """Signed but irrelevant: an unknown order, an unsubscribed event, an unreadable body.
    Answered 200 so the gateway stops retrying; nothing written."""
    sale, order_id = await _ready(api, sold)
    stranger = gateway.orders[order_id]
    gateway.orders["order_unknown1"] = stranger.__class__(
        "order_unknown1", 10_500, "INR", "x", "created"
    )
    unknown = gateway.pay("order_unknown1")
    before = await state(db, sale)
    authorized = gateway.pay(order_id, "authorized")
    garbage = b'{"event": 5}'
    for raw, headers in (
        gateway.webhook("payment.captured", unknown),
        gateway.webhook("payment.authorized", authorized),
        (garbage, gateway.sign_webhook(garbage)),
    ):
        response = await _post(api, raw, headers)
        assert response.status_code == 200
    assert await state(db, sale) == before


async def test_database_error_asks_for_a_retry(
    api: AsyncClient,
    sold,
    gateway: FakeGateway,
    payment_service: PaymentService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 5xx makes the gateway redeliver; the event is not lost."""
    sale, order_id = await _ready(api, sold)
    paid = gateway.pay(order_id)

    async def broken(*_: object) -> None:
        raise OperationalError("SELECT", {}, Exception("connection lost"))

    monkeypatch.setattr(PaymentRepository, "lock_by_order", broken)
    response = await _post(api, *gateway.webhook("payment.captured", paid))
    assert response.status_code == 500
