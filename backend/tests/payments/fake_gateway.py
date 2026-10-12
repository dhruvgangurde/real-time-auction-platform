"""In-memory payment gateway for tests: no network (SI-3). Behaves like the gateway's test mode
for what the platform relies on: orders with receipts, payments per order, capture, and signed
checkout callbacks and webhooks. Can be taken down, or made to accept duplicate receipts."""

import hashlib
import hmac
import json
from dataclasses import replace
from typing import Any
from uuid import uuid4

from app.services.payment_gateway import (
    GatewayOrder,
    GatewayPayment,
    GatewayRejected,
    GatewayUnavailable,
)


def _sign(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


class FakeGateway:
    key_id = "rzp_test_fake"

    def __init__(self, key_secret: str, webhook_secret: str) -> None:
        self.key_secret, self.webhook_secret = key_secret, webhook_secret
        self.orders: dict[str, GatewayOrder] = {}
        self.payments: dict[str, GatewayPayment] = {}
        self.calls: list[str] = []
        self.down = False
        self.unique_receipts = True

    def _call(self, name: str) -> None:
        self.calls.append(name)
        if self.down:
            raise GatewayUnavailable("fake gateway down")

    async def create_order(
        self, *, amount: int, currency: str, receipt: str, notes: dict[str, str]
    ) -> GatewayOrder:
        self._call("create_order")
        if self.unique_receipts and any(o.receipt == receipt for o in self.orders.values()):
            raise GatewayRejected("Receipt already exists for another order")
        if amount < 100:
            raise GatewayRejected("Order amount less than minimum amount allowed")
        order = GatewayOrder(f"order_{uuid4().hex[:14]}", amount, currency, receipt, "created")
        self.orders[order.id] = order
        return order

    async def find_order(self, receipt: str) -> GatewayOrder | None:
        self._call("find_order")
        return next((o for o in self.orders.values() if o.receipt == receipt), None)

    async def order_payments(self, order_id: str) -> list[GatewayPayment]:
        self._call("order_payments")
        return [p for p in self.payments.values() if p.order_id == order_id]

    async def capture(self, payment_id: str, amount: int, currency: str) -> GatewayPayment:
        self._call("capture")
        payment = self.payments[payment_id]
        if payment.status == "captured":
            raise GatewayRejected("This payment has already been captured")
        if payment.status != "authorized" or amount != payment.amount:
            raise GatewayRejected("Capture amount must equal the authorized amount")
        return self.set_status(payment_id, "captured")

    # ---------------------------------------------------------------- what a checkout does

    def pay(
        self,
        order_id: str,
        status: str = "captured",
        *,
        amount: int | None = None,
        error_code: str | None = None,
    ) -> str:
        order = self.orders[order_id]
        payment = GatewayPayment(
            id=f"pay_{uuid4().hex[:14]}",
            order_id=order_id,
            amount=order.amount if amount is None else amount,
            currency=order.currency,
            status=status,
            error_code=error_code or ("BAD_REQUEST_ERROR" if status == "failed" else None),
        )
        self.payments[payment.id] = payment
        return payment.id

    def set_status(self, payment_id: str, status: str) -> GatewayPayment:
        self.payments[payment_id] = replace(self.payments[payment_id], status=status)
        return self.payments[payment_id]

    def checkout_signature(self, order_id: str, payment_id: str) -> str:
        return _sign(self.key_secret, f"{order_id}|{payment_id}".encode())

    def webhook(
        self, event: str, payment_id: str, event_id: str | None = None
    ) -> tuple[bytes, dict[str, str]]:
        """A webhook delivery as the gateway sends it: raw JSON body and signed headers. The
        entity carries contact details the platform must never store or log."""
        p = self.payments[payment_id]
        entity: dict[str, Any] = {
            "id": p.id,
            "entity": "payment",
            "amount": p.amount,
            "currency": p.currency,
            "status": "captured" if event == "payment.captured" else p.status,
            "order_id": p.order_id,
            "method": "card",
            "error_code": p.error_code,
            "email": "payer@example.com",
            "contact": "+919900000000",
        }
        body = {
            "entity": "event",
            "account_id": "acc_test",
            "event": event,
            "contains": ["payment"],
            "payload": {"payment": {"entity": entity}},
            "created_at": 1_790_000_000,
        }
        raw = json.dumps(body, separators=(",", ":")).encode()
        return raw, self.sign_webhook(raw, event_id)

    def sign_webhook(self, raw: bytes, event_id: str | None = None) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-Razorpay-Signature": _sign(self.webhook_secret, raw),
            "X-Razorpay-Event-Id": event_id or f"evt_{uuid4().hex[:14]}",
        }
