"""Payment endpoints (FR-65 to FR-69, NFR-25, DC-10). Routing only; the rules live in
PaymentService.

The checkout and the confirmation need the signed-in winner. The webhook has no session: the
gateway's signature over the raw body is the only thing that admits it, checked before parsing.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from app.api.deps import client_ip, current_user, get_payment_service
from app.core.config import settings
from app.schemas.payments import PaymentConfirm
from app.services.auth import CurrentUser
from app.services.images import read_capped
from app.services.payments import PaymentService

router = APIRouter(prefix="/api/v1", tags=["payments"])


@router.post("/auctions/{auction_id}/payment")
async def start_payment(
    auction_id: UUID,
    user: CurrentUser = Depends(current_user),
    payments: PaymentService = Depends(get_payment_service),
) -> dict[str, Any]:
    """The gateway order for the caller's won auction; the amount is the settlement's."""
    return await payments.start(user, auction_id)


@router.post("/auctions/{auction_id}/payment/confirm")
async def confirm_payment(
    auction_id: UUID,
    body: PaymentConfirm,
    response: Response,
    user: CurrentUser = Depends(current_user),
    payments: PaymentService = Depends(get_payment_service),
) -> dict[str, Any]:
    status, result = await payments.confirm(
        user,
        auction_id,
        body.razorpay_order_id,
        body.razorpay_payment_id,
        body.razorpay_signature,
    )
    response.status_code = status
    return result


@router.post("/payments/razorpay/webhook")
async def razorpay_webhook(
    request: Request, payments: PaymentService = Depends(get_payment_service)
) -> dict[str, str]:
    raw = await read_capped(
        request.stream(),
        settings.payment_webhook_max_bytes,
        code="PAYLOAD_TOO_LARGE",
        what="webhook bodies",
    )
    return await payments.webhook(
        raw,
        request.headers.get("x-razorpay-signature"),
        request.headers.get("x-razorpay-event-id"),
        client_ip(request),
    )
