"""Payment gateway boundary (SI-3, FR-66, DC-10, AS-5): Razorpay in test mode.

The platform creates orders and reads payments; card entry happens only in Razorpay Checkout,
so no card data ever reaches these servers. Signatures are verified locally with
constant-time comparison (FR-67, NFR-25):

- checkout callback: hex HMAC-SHA256(key_secret, "<order_id>|<payment_id>")
- webhook:           hex HMAC-SHA256(webhook_secret, raw request body), X-Razorpay-Signature

Tests substitute an in-memory gateway; nothing here is called from bidding, closing or
settlement creation (DP-1).
"""

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.core.config import settings

CURRENCY = "INR"  # Release 1 settles in rupees only; amounts are integer paise (DC-6)


@dataclass(frozen=True)
class GatewayOrder:
    id: str
    amount: int
    currency: str
    receipt: str | None
    status: str  # created | attempted | paid


@dataclass(frozen=True)
class GatewayPayment:
    id: str
    order_id: str | None
    amount: int
    currency: str
    status: str  # created | authorized | captured | refunded | failed
    error_code: str | None = None


class GatewayUnavailable(Exception):
    """Network error, timeout, 5xx or 429: try again later (DP-1)."""


class GatewayRejected(Exception):
    """The gateway refused the request (4xx). `description` comes from the gateway, never from
    the client, and carries no card data."""

    def __init__(self, description: str) -> None:
        super().__init__(description)
        self.description = description

    @property
    def duplicate_receipt(self) -> bool:
        return "receipt" in self.description.lower()

    @property
    def already_captured(self) -> bool:
        text = self.description.lower()
        return "already" in text and ("captured" in text or "paid" in text)


class PaymentGateway(Protocol):
    key_id: str  # public, handed to the checkout

    async def create_order(
        self, *, amount: int, currency: str, receipt: str, notes: dict[str, str]
    ) -> GatewayOrder: ...

    async def find_order(self, receipt: str) -> GatewayOrder | None: ...

    async def order_payments(self, order_id: str) -> list[GatewayPayment]: ...

    async def capture(self, payment_id: str, amount: int, currency: str) -> GatewayPayment: ...


def _hmac_hex(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_checkout_signature(
    key_secret: str, order_id: str, payment_id: str, signature: str
) -> bool:
    if not key_secret:
        return False
    expected = _hmac_hex(key_secret, f"{order_id}|{payment_id}".encode())
    return hmac.compare_digest(expected, signature)


def verify_webhook_signature(webhook_secret: str, raw_body: bytes, signature: str | None) -> bool:
    """Over the raw bytes as received, before any parsing (FR-67, NFR-25)."""
    if not webhook_secret or not signature:
        return False
    return hmac.compare_digest(_hmac_hex(webhook_secret, raw_body), signature)


def _order(data: dict[str, Any]) -> GatewayOrder:
    return GatewayOrder(
        id=data["id"],
        amount=int(data["amount"]),
        currency=data["currency"],
        receipt=data.get("receipt"),
        status=data["status"],
    )


def _payment(data: dict[str, Any]) -> GatewayPayment:
    return GatewayPayment(
        id=data["id"],
        order_id=data.get("order_id"),
        amount=int(data["amount"]),
        currency=data["currency"],
        status=data["status"],
        error_code=data.get("error_code"),
    )


class RazorpayGateway:
    """The Razorpay REST API over HTTPS with basic auth. No SDK: four calls."""

    def __init__(
        self,
        key_id: str,
        key_secret: str,
        base_url: str,
        timeout_s: float,
        transport: httpx.AsyncBaseTransport | None = None,  # tests: httpx.MockTransport
    ) -> None:
        self.key_id = key_id
        self._auth = (key_id, key_secret)
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_s
        self._transport = transport

    async def _call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if not self._auth[0] or not self._auth[1]:
            raise GatewayUnavailable("payment gateway not configured")
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                auth=self._auth,
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise GatewayUnavailable(type(exc).__name__) from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise GatewayUnavailable(f"gateway status {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise GatewayUnavailable("gateway sent a non-JSON response") from exc
        if response.status_code >= 400:
            error = body.get("error", {}) if isinstance(body, dict) else {}
            raise GatewayRejected(str(error.get("description") or response.status_code))
        return body

    async def create_order(
        self, *, amount: int, currency: str, receipt: str, notes: dict[str, str]
    ) -> GatewayOrder:
        body = {"amount": amount, "currency": currency, "receipt": receipt, "notes": notes}
        return _order(await self._call("POST", "/orders", json=body))

    async def find_order(self, receipt: str) -> GatewayOrder | None:
        items = (await self._call("GET", "/orders", params={"receipt": receipt}))["items"]
        return _order(items[0]) if items else None

    async def order_payments(self, order_id: str) -> list[GatewayPayment]:
        body = await self._call("GET", f"/orders/{order_id}/payments")
        return [_payment(p) for p in body["items"]]

    async def capture(self, payment_id: str, amount: int, currency: str) -> GatewayPayment:
        body = {"amount": amount, "currency": currency}
        return _payment(await self._call("POST", f"/payments/{payment_id}/capture", json=body))


def razorpay_from_settings() -> RazorpayGateway:
    return RazorpayGateway(
        settings.razorpay_key_id,
        settings.razorpay_key_secret,
        settings.razorpay_api_base,
        settings.razorpay_timeout_s,
    )
