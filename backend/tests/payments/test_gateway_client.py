"""The Razorpay client's requests and error mapping (SI-3, DP-1), against httpx.MockTransport:
no network. What it sends: basic auth with the key pair, amounts in paise, the receipt."""

import base64
import json
from collections.abc import Callable

import httpx
import pytest

from app.services.payment_gateway import (
    GatewayRejected,
    GatewayUnavailable,
    RazorpayGateway,
    razorpay_from_settings,
)

ORDER = {
    "id": "order_A1",
    "amount": 10_500,
    "currency": "INR",
    "receipt": "s-1",
    "status": "created",
}
PAYMENT = {
    "id": "pay_B2",
    "order_id": "order_A1",
    "amount": 10_500,
    "currency": "INR",
    "status": "captured",
    "error_code": None,
    "email": "payer@example.com",
}


def _gateway(handler: Callable[[httpx.Request], httpx.Response]) -> RazorpayGateway:
    return RazorpayGateway(
        "rzp_test_key", "secret", "https://api.example.test/v1", 5, httpx.MockTransport(handler)
    )


async def test_requests_and_parsing() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if request.method == "POST" and path == "/v1/orders":
            return httpx.Response(200, json=ORDER)
        if path == "/v1/orders" and request.url.params.get("receipt") == "s-1":
            return httpx.Response(200, json={"entity": "collection", "items": [ORDER]})
        if path == "/v1/orders":
            return httpx.Response(200, json={"entity": "collection", "items": []})
        if path == "/v1/orders/order_A1/payments":
            return httpx.Response(200, json={"items": [PAYMENT]})
        if path == "/v1/payments/pay_B2/capture":
            return httpx.Response(200, json=PAYMENT)
        return httpx.Response(404, json={"error": {"description": "not found"}})

    gateway = _gateway(handler)
    order = await gateway.create_order(
        amount=10_500, currency="INR", receipt="s-1", notes={"settlement_id": "s-1"}
    )
    assert (order.id, order.amount, order.receipt) == ("order_A1", 10_500, "s-1")
    assert (await gateway.find_order("s-1")) == order
    assert await gateway.find_order("other") is None
    (payment,) = await gateway.order_payments("order_A1")
    assert (payment.id, payment.status, payment.amount) == ("pay_B2", "captured", 10_500)
    assert (await gateway.capture("pay_B2", 10_500, "INR")).status == "captured"

    create = seen[0]
    assert json.loads(create.content) == {
        "amount": 10_500,
        "currency": "INR",
        "receipt": "s-1",
        "notes": {"settlement_id": "s-1"},
    }
    token = base64.b64encode(b"rzp_test_key:secret").decode()
    assert all(r.headers["authorization"] == f"Basic {token}" for r in seen)
    assert json.loads(seen[-1].content) == {"amount": 10_500, "currency": "INR"}


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(500, text="oops"), GatewayUnavailable),
        (httpx.Response(429, json={}), GatewayUnavailable),
        (httpx.Response(200, text="<html>proxy</html>"), GatewayUnavailable),
        (
            httpx.Response(400, json={"error": {"description": "Receipt already exists"}}),
            GatewayRejected,
        ),
        (httpx.Response(401, json={"error": {}}), GatewayRejected),
    ],
)
async def test_errors_are_mapped(response: httpx.Response, error: type[Exception]) -> None:
    gateway = _gateway(lambda _: response)
    with pytest.raises(error):
        await gateway.order_payments("order_A1")


async def test_network_failures_and_missing_keys_are_unavailable() -> None:
    def broken(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(GatewayUnavailable):
        await _gateway(broken).order_payments("order_A1")
    unconfigured = RazorpayGateway("", "", "https://api.example.test/v1", 5)
    with pytest.raises(GatewayUnavailable, match="not configured"):
        await unconfigured.order_payments("order_A1")
    assert razorpay_from_settings().key_id is not None
