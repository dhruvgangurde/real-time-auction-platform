"""Gateway signatures (FR-67, NFR-25, SI-3) and the test-mode-only rule (AS-5)."""

import pytest

from app.core import security
from app.core.config import settings
from app.services.payment_gateway import (
    GatewayRejected,
    verify_checkout_signature,
    verify_webhook_signature,
)

CHECKOUT_VECTOR = "05a90d99a226250bdd07dcbec806d936d0ac974af71513b19a36466e7f5eb3a3"
WEBHOOK_VECTOR = "4f463a57dd128675850163391f0311888616d57bccca75c774c9cdb28134f851"
WEBHOOK_BODY = b'{"event":"payment.captured"}'


def test_checkout_signature_is_hmac_of_order_and_payment() -> None:
    order, payment = "order_9A33XWu170gUtm", "pay_29QQoUBi66xm2f"
    assert verify_checkout_signature("test_key_secret", order, payment, CHECKOUT_VECTOR)
    assert not verify_checkout_signature("test_key_secret", order, "pay_other", CHECKOUT_VECTOR)
    assert not verify_checkout_signature("other_secret", order, payment, CHECKOUT_VECTOR)
    assert not verify_checkout_signature("", order, payment, CHECKOUT_VECTOR)


def test_webhook_signature_is_over_the_raw_body() -> None:
    assert verify_webhook_signature("whsec_test", WEBHOOK_BODY, WEBHOOK_VECTOR)
    # the same JSON with different whitespace is a different body
    assert not verify_webhook_signature(
        "whsec_test", b'{"event": "payment.captured"}', WEBHOOK_VECTOR
    )
    assert not verify_webhook_signature("whsec_test", WEBHOOK_BODY, None)
    assert not verify_webhook_signature("", WEBHOOK_BODY, WEBHOOK_VECTOR)


def test_gateway_rejection_kinds() -> None:
    assert GatewayRejected("Receipt already exists").duplicate_receipt
    assert GatewayRejected("This payment has already been captured").already_captured
    assert GatewayRejected("Order is already paid").already_captured
    assert not GatewayRejected("Order amount less than minimum").already_captured


@pytest.mark.parametrize("app_env", ["local", "production"])
def test_live_keys_are_refused_everywhere(monkeypatch: pytest.MonkeyPatch, app_env: str) -> None:
    """AS-5: Release 1 never moves real funds."""
    monkeypatch.setattr(settings, "app_env", app_env)
    monkeypatch.setattr(settings, "razorpay_key_id", "rzp_live_abcdef")
    with pytest.raises(RuntimeError, match="test-mode key"):
        security.check_startup_config()


def test_gateway_secrets_required_outside_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "jwt_secret", "j" * 32)
    monkeypatch.setattr(settings, "alias_secret", "a" * 32)
    monkeypatch.setattr(settings, "email_backend", "smtp")
    monkeypatch.setattr(settings, "razorpay_key_id", "rzp_test_abc")
    monkeypatch.setattr(settings, "razorpay_key_secret", "")
    with pytest.raises(RuntimeError, match="_KEY_SECRET"):
        security.check_startup_config()
    monkeypatch.setattr(settings, "razorpay_key_secret", "s")
    monkeypatch.setattr(settings, "razorpay_webhook_secret", "w")
    security.check_startup_config()
