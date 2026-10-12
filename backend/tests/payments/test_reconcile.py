"""Payment reconciliation (FR-69, SRS 4.9.2 S4): a charge whose callback and webhook never
arrived is completed from the gateway; the oldest-checked orders go first; it is safe alongside
the webhook."""

import asyncpg
import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.services.payments import PaymentService
from tests.payments.fake_gateway import FakeGateway
from tests.payments.support import Sale, start, state


async def _backdate(db: asyncpg.Connection, sale: Sale, seconds: int) -> None:
    """As if the last payment activity was `seconds` ago."""
    await db.execute(
        "UPDATE payments SET updated_at = now() - make_interval(secs => $2) "
        "WHERE settlement_id = $1",
        sale.settlement_id,
        float(seconds),
    )


async def _quiet(db: asyncpg.Connection) -> None:
    """The reconciler looks at every payment: earlier tests' orders must not count."""
    await db.execute(
        "UPDATE payments SET updated_at = now() - interval '30 days' WHERE status <> 'succeeded'"
    )


async def test_lost_confirmation_is_completed(
    api: AsyncClient,
    sold,
    gateway: FakeGateway,
    payment_service: PaymentService,
    db: asyncpg.Connection,
) -> None:
    """S4: charged at the gateway, nothing ever told us; the reconciler settles it once."""
    await _quiet(db)
    sale = await sold()
    order_id = (await start(api, sale))["order_id"]
    gateway.pay(order_id)
    assert await payment_service.reconcile() == {}  # fresh orders are left to the callback
    await _backdate(db, sale, 300)
    assert await payment_service.reconcile() == {"paid": 1}
    after = await state(db, sale)
    assert (after["settlement"], after["payment"], after["events"]) == ("paid", "succeeded", 1)
    assert await payment_service.reconcile() == {}  # paid settlements are not checked again
    key = await db.fetchval(
        "SELECT dedupe_key FROM payment_events WHERE payment_id = "
        "(SELECT id FROM payments WHERE settlement_id = $1)",
        sale.settlement_id,
    )
    assert key.startswith("reconcile:pay_") and key.endswith(":succeeded")


async def test_gateway_errors_are_retried_next_run(
    api: AsyncClient,
    sold,
    gateway: FakeGateway,
    payment_service: PaymentService,
    db: asyncpg.Connection,
) -> None:
    await _quiet(db)
    sale = await sold()
    order_id = (await start(api, sale))["order_id"]
    gateway.pay(order_id)
    await _backdate(db, sale, 300)
    gateway.down = True
    assert await payment_service.reconcile() == {"pending": 1}
    assert (await state(db, sale))["settlement"] == "pending_payment"
    gateway.down = False
    assert await payment_service.reconcile() == {"paid": 1}


async def test_least_recently_checked_first(
    api: AsyncClient,
    sold,
    gateway: FakeGateway,
    payment_service: PaymentService,
    db: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A backlog larger than one batch cannot starve newer orders: never-checked orders go
    first, then the longest unchecked."""
    await _quiet(db)
    monkeypatch.setattr(settings, "payment_reconcile_batch", 2)
    sales = [await sold() for _ in range(3)]
    for sale in sales:
        await start(api, sale)
        await _backdate(db, sale, 300)

    async def checked() -> set:
        rows = await db.fetch(
            "SELECT settlement_id FROM payments "
            "WHERE reconciled_at IS NOT NULL AND settlement_id = ANY($1::uuid[])",
            [s.settlement_id for s in sales],
        )
        return {r["settlement_id"] for r in rows}

    assert await payment_service.reconcile() == {"pending": 2}
    first = await checked()
    assert len(first) == 2
    await payment_service.reconcile()
    assert await checked() == {s.settlement_id for s in sales}  # the third went first


async def test_lookback_counts_from_the_last_activity(
    api: AsyncClient,
    sold,
    gateway: FakeGateway,
    payment_service: PaymentService,
    db: asyncpg.Connection,
) -> None:
    """An order last touched eight days ago is outside the window; the winner coming back to
    pay touches it, so a payment on day eight is still reconciled."""
    await _quiet(db)
    sale = await sold()
    order_id = (await start(api, sale))["order_id"]
    gateway.pay(order_id)
    await _backdate(db, sale, 8 * 86_400)
    assert await payment_service.reconcile() == {}
    await start(api, sale)  # the winner opens the payment page again
    await _backdate(db, sale, 300)
    assert await payment_service.reconcile() == {"paid": 1}


async def test_failure_applied_once_across_sources(
    api: AsyncClient,
    sold,
    gateway: FakeGateway,
    payment_service: PaymentService,
    db: asyncpg.Connection,
) -> None:
    """FR-68: the same failed gateway payment seen by the reconciler twice and by a webhook is
    one failure: one audit row, one payment_failed notification."""
    await _quiet(db)
    sale = await sold()
    order_id = (await start(api, sale))["order_id"]
    failed = gateway.pay(order_id, "failed")
    await _backdate(db, sale, 300)
    assert await payment_service.reconcile() == {"failed": 1}
    await _backdate(db, sale, 300)
    assert await payment_service.reconcile() == {"failed": 1}  # no new effect
    raw, headers = gateway.webhook("payment.failed", failed)
    await api.post("/api/v1/payments/razorpay/webhook", content=raw, headers=headers)
    after = await state(db, sale)
    assert after["payment_audits"] == ["payment_failed"]
    assert after["notifications"] == [("payment_failed", sale.winner_id)]
    assert after["events"] == 1
