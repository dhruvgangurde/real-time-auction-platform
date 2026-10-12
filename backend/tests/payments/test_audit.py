"""Audit rows of payments (FR-77, FR-78, FR-64): the exact state before and after for the order,
a success, a failure and the settlement's pending_payment -> paid, through each source."""

import json
from typing import Any
from uuid import UUID

import asyncpg
from httpx import AsyncClient

from app.services.payments import PaymentService
from tests.payments.fake_gateway import FakeGateway
from tests.payments.support import WEBHOOK, Sale, confirm, start


async def _audits(db: asyncpg.Connection, sale: Sale) -> list[dict[str, Any]]:
    """Payment and settlement audit rows written after the close, oldest first."""
    rows = await db.fetch(
        "SELECT l.entity_type, l.action, l.actor_id, l.before, l.after FROM audit_log l "
        "WHERE (l.entity_type = 'payment' AND l.entity_id IN "
        "(SELECT id::text FROM payments WHERE settlement_id = $1)) "
        "OR (l.entity_type = 'settlement' AND l.entity_id = $2 "
        "AND l.action = 'status_changed') ORDER BY l.id",
        sale.settlement_id,
        str(sale.settlement_id),
    )
    return [
        {
            "entity": r["entity_type"],
            "action": r["action"],
            "actor": r["actor_id"],
            "before": json.loads(r["before"]) if r["before"] else None,
            "after": json.loads(r["after"]),
        }
        for r in rows
    ]


def _created(sale: Sale, view: dict[str, Any]) -> dict[str, Any]:
    return {
        "entity": "payment",
        "action": "payment_created",
        "actor": sale.winner_id,
        "before": None,
        "after": {
            "settlement_id": str(sale.settlement_id),
            "amount": 10_500,
            "order_id": view["order_id"],
        },
    }


def _succeeded(before: str, pay: str, source: str, key: str) -> dict[str, Any]:
    return {
        "entity": "payment",
        "action": "payment_succeeded",
        "actor": None,
        "before": {"status": before},
        "after": {
            "status": "succeeded",
            "gateway_payment_id": pay,
            "source": source,
            "event": "payment.captured",
            "dedupe_key": key,
        },
    }


def _failed(before: str, pay: str, source: str, key: str) -> dict[str, Any]:
    return {
        "entity": "payment",
        "action": "payment_failed",
        "actor": None,
        "before": {"status": before},
        "after": {
            "status": "failed",
            "gateway_payment_id": pay,
            "error_code": "BAD_REQUEST_ERROR",
            "source": source,
            "event": "payment.failed",
            "dedupe_key": key,
        },
    }


def _paid(view: dict[str, Any]) -> dict[str, Any]:
    return {
        "entity": "settlement",
        "action": "status_changed",
        "actor": None,
        "before": {"status": "pending_payment"},
        "after": {"status": "paid", "payment_id": view["payment_id"]},
    }


async def test_audit_through_the_checkout_callback(
    api: AsyncClient,
    sold,
    gateway: FakeGateway,
    payment_service: PaymentService,
    db: asyncpg.Connection,
) -> None:
    sale = await sold()
    view = await start(api, sale)
    failed = gateway.pay(view["order_id"], "failed")
    await confirm(api, sale, gateway, view["order_id"], failed)
    paid = gateway.pay(view["order_id"])
    await confirm(api, sale, gateway, view["order_id"], paid)
    assert await _audits(db, sale) == [
        _created(sale, view),
        _failed("created", failed, "callback", f"callback:{failed}:failed"),
        _succeeded("failed", paid, "callback", f"callback:{paid}:succeeded"),
        _paid(view),
    ]


async def test_audit_through_the_webhook(
    api: AsyncClient,
    sold,
    gateway: FakeGateway,
    payment_service: PaymentService,
    db: asyncpg.Connection,
) -> None:
    sale = await sold()
    view = await start(api, sale)
    paid = gateway.pay(view["order_id"])
    raw, headers = gateway.webhook("payment.captured", paid, event_id="evt_audit_ok")
    assert (await api.post(WEBHOOK, content=raw, headers=headers)).status_code == 200
    assert await _audits(db, sale) == [
        _created(sale, view),
        _succeeded("created", paid, "webhook", "webhook:evt_audit_ok"),
        _paid(view),
    ]

    other = await sold()
    other_view = await start(api, other)
    failed = gateway.pay(other_view["order_id"], "failed")
    raw, headers = gateway.webhook("payment.failed", failed, event_id="evt_audit_fail")
    assert (await api.post(WEBHOOK, content=raw, headers=headers)).status_code == 200
    assert await _audits(db, other) == [
        _created(other, other_view),
        _failed("created", failed, "webhook", "webhook:evt_audit_fail"),
    ]


async def test_audit_through_the_reconciler(
    api: AsyncClient,
    sold,
    gateway: FakeGateway,
    payment_service: PaymentService,
    db: asyncpg.Connection,
) -> None:
    await db.execute(
        "UPDATE payments SET updated_at = now() - interval '30 days' WHERE status <> 'succeeded'"
    )
    sale = await sold()
    view = await start(api, sale)
    failed = gateway.pay(view["order_id"], "failed")

    async def reconcile() -> dict[str, int]:
        await db.execute(
            "UPDATE payments SET updated_at = now() - interval '5 minutes' "
            "WHERE settlement_id = $1",
            sale.settlement_id,
        )
        return await payment_service.reconcile()

    assert await reconcile() == {"failed": 1}
    paid = gateway.pay(view["order_id"])
    assert await reconcile() == {"paid": 1}
    assert await _audits(db, sale) == [
        _created(sale, view),
        _failed("created", failed, "reconcile", f"reconcile:{failed}:failed"),
        _succeeded("failed", paid, "reconcile", f"reconcile:{paid}:succeeded"),
        _paid(view),
    ]
    assert isinstance(sale.winner_id, UUID)
