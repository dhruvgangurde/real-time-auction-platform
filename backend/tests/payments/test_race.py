"""Exactly once under races (FR-67, FR-68, NFR-11, DC-5): checkout callbacks, webhooks
(including redeliveries and stale failures) and the reconciler, in any order and at once."""

import asyncio
from uuid import uuid4

import asyncpg
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.repositories import PaymentRepository
from app.services.auth import CurrentUser
from app.services.payments import PaymentService
from tests.payments.fake_gateway import FakeGateway
from tests.payments.support import Sale, state


def _winner(sale: Sale) -> CurrentUser:
    return CurrentUser(sale.winner_id, ("bidder",), uuid4(), 0)


async def test_callback_webhook_and_reconcile_racing(
    sold,
    gateway: FakeGateway,
    payment_service: PaymentService,
    db: asyncpg.Connection,
    sessions,
) -> None:
    """Ten of each source at once: one transition, one audit row per entity, one notification
    per party, one applied event."""
    sale = await sold()
    order_id = (await payment_service.start(_winner(sale), sale.auction_id))["order_id"]
    paid = gateway.pay(order_id)
    signature = gateway.checkout_signature(order_id, paid)
    async with sessions() as session:
        payment = await PaymentRepository(session).for_settlement(sale.settlement_id)
    deliveries = [gateway.webhook("payment.captured", paid) for _ in range(5)]
    deliveries += deliveries  # each delivered twice
    calls = []
    for i in range(10):
        calls.append(
            payment_service.confirm(_winner(sale), sale.auction_id, order_id, paid, signature)
        )
        raw, headers = deliveries[i]
        calls.append(
            payment_service.webhook(
                raw, headers["X-Razorpay-Signature"], headers["X-Razorpay-Event-Id"], "10.9.9.9"
            )
        )
        calls.append(payment_service.sync_order(payment, "reconcile"))
    await asyncio.gather(*calls)
    assert await state(db, sale) == {
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


SCENARIOS = st.sampled_from(["captured", "authorized", "failed", "failed_then_captured"])
OPS = st.lists(
    st.sampled_from(["callback", "webhook", "stale_failure", "redeliver", "reconcile"]),
    min_size=1,
    max_size=8,
)


@settings(
    max_examples=25,  # each example settles a fresh auction: about a second each
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(scenario=SCENARIOS, ops=OPS, together=st.booleans())
async def test_any_interleaving_settles_at_most_once(
    sold,
    gateway: FakeGateway,
    payment_service: PaymentService,
    db: asyncpg.Connection,
    sessions,
    scenario: str,
    ops: list[str],
    together: bool,
) -> None:
    """Property (FR-67, FR-68, NFR-11): whatever arrives, in whatever order or concurrency,
    the settlement is paid only if a matching capture exists, at most one success is applied,
    a success is never undone, and each failed attempt is reported at most once."""
    sale = await sold()
    order_id = (await payment_service.start(_winner(sale), sale.auction_id))["order_id"]
    failed = gateway.pay(order_id, "failed") if "failed" in scenario else None
    latest = (
        failed
        if scenario == "failed"
        else gateway.pay(order_id, "authorized" if scenario == "authorized" else "captured")
    )
    async with sessions() as session:
        payment = await PaymentRepository(session).for_settlement(sale.settlement_id)
    first_delivery: list[tuple[bytes, dict[str, str]]] = []

    def webhook_call(event: str, payment_id: str, again: bool = False):
        if again and first_delivery:
            raw, headers = first_delivery[0]
        else:
            raw, headers = gateway.webhook(event, payment_id)
            first_delivery.append((raw, headers))
        return payment_service.webhook(
            raw, headers["X-Razorpay-Signature"], headers["X-Razorpay-Event-Id"], "10.8.8.8"
        )

    def call(op: str):
        status = gateway.payments[latest].status
        event = {"captured": "payment.captured", "failed": "payment.failed"}.get(
            status, "payment.authorized"
        )
        if op == "callback":
            signature = gateway.checkout_signature(order_id, latest)
            return payment_service.confirm(
                _winner(sale), sale.auction_id, order_id, latest, signature
            )
        if op == "webhook":
            return webhook_call(event, latest)
        if op == "stale_failure" and failed:
            return webhook_call("payment.failed", failed)
        if op == "redeliver":
            return webhook_call(event, latest, again=True)
        return payment_service.sync_order(payment, "reconcile")

    if together:
        await asyncio.gather(*(call(op) for op in ops))
    else:
        for op in ops:
            await call(op)

    after = await state(db, sale)
    captured = [
        p for p in gateway.payments.values() if p.order_id == order_id and p.status == "captured"
    ]
    if after["settlement"] == "paid":
        assert captured and after["gateway_payment_id"] in {p.id for p in captured}
        assert after["payment"] == "succeeded"
    assert after["payment_audits"].count("payment_succeeded") <= 1
    assert after["settlement_audits"] <= 1
    received = [n for n in after["notifications"] if n[0] == "payment_received"]
    assert len(received) in (0, 2)
    assert len([n for n in after["notifications"] if n[0] == "payment_failed"]) <= 1
    if after["payment"] == "succeeded":
        assert after["settlement"] == "paid"  # never paid on one side only
    pulled = {"callback", "reconcile"} & set(ops)
    if captured and scenario != "failed" and (pulled or "webhook" in ops):
        assert after["settlement"] == "paid"  # liveness: an observed capture is applied
