"""Payments through the gateway in test mode (FR-65 to FR-69, NFR-25, DC-5, DC-10, DP-1, SI-3).

One payment row per settlement, one gateway order per payment row. Card entry happens only in
the gateway's checkout (DC-10). Three sources confirm an outcome, in any order, any number of
times, concurrently:

- the checkout callback (signed by the gateway with the key secret, then the order's payments
  are read from the gateway: `sync_order`);
- the webhook (the raw body signed with the webhook secret, applied from the signed payload);
- the reconciler (`sync_order` for unpaid orders, so a lost callback never leaves a charged
  order unpaid, FR-69).

All three end in `_apply`, one transaction serialised on the payment row lock: the settlement
moves to paid exactly once (conditional update, UNIQUE gateway_payment_id, one succeeded
payment per settlement), with its audit rows and notifications. An applied outcome records its
event once (payment_events.dedupe_key); a sync that changes nothing records nothing. A gateway
outage blocks payment only (DP-1): nothing in bidding, closing or settlement calls it.
"""

import logging
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.errors import AppError
from app.core.ratelimit import RateLimit, RateLimiter
from app.models import Notification, Payment, Settlement
from app.repositories import (
    AuctionRepository,
    AuditLogRepository,
    NotificationRepository,
    PaymentEventRepository,
    PaymentRepository,
    SettlementRepository,
)
from app.services import live_events
from app.services.auth import CurrentUser
from app.services.live_state import to_ms
from app.services.payment_gateway import (
    CURRENCY,
    GatewayOrder,
    GatewayPayment,
    GatewayRejected,
    GatewayUnavailable,
    PaymentGateway,
    verify_checkout_signature,
    verify_webhook_signature,
)
from app.services.snapshot import visible

log = logging.getLogger("payments")

SUCCESS_EVENTS = frozenset({"payment.captured"})
FAILURE_EVENTS = frozenset({"payment.failed"})
_EVENT_ID = re.compile(r"^[A-Za-z0-9_]{1,64}$")


def _unavailable(detail: str) -> AppError:
    return AppError("SERVICE_UNAVAILABLE", f"payment gateway unavailable: {detail}", 503)


# ------------------------------------------------------------------ webhook payload (DC-11)


class _Entity(BaseModel):
    model_config = ConfigDict(extra="ignore")  # the gateway sends many more fields
    id: str = Field(pattern=r"^pay_[A-Za-z0-9]{1,36}$")
    order_id: str | None = Field(default=None, max_length=64)
    amount: int = Field(ge=0)
    currency: str = Field(max_length=3)
    status: str = Field(max_length=32)
    error_code: str | None = Field(default=None, max_length=128)


class _Wrapped(BaseModel):
    model_config = ConfigDict(extra="ignore")
    entity: _Entity


class _Payload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    payment: _Wrapped | None = None


class WebhookBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    event: str = Field(max_length=64)
    payload: _Payload


@dataclass(frozen=True)
class Confirmation:
    """A gateway outcome for one gateway payment, from one source."""

    source: str  # callback | webhook | reconcile
    event: str
    dedupe_key: str
    gateway_payment_id: str
    order_id: str
    amount: int
    currency: str
    succeeded: bool
    error_code: str | None = None


def payment_view(payment: Payment | None) -> dict[str, Any] | None:
    """FR-69: the payment status the winner and the seller see on the settlement."""
    if payment is None:
        return None
    return {
        "id": str(payment.id),
        "status": payment.status,
        "amount": payment.amount,
        "currency": CURRENCY,
        "order_id": payment.gateway_order_id,
        "payment_id": payment.gateway_payment_id,
        "updated_at_ms": to_ms(payment.updated_at),
    }


class PaymentService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        redis: Redis,
        gateway: PaymentGateway,
        limiter: RateLimiter,
        *,
        key_secret: str,
        webhook_secret: str,
    ) -> None:
        self._sessions, self._redis, self._gateway = sessions, redis, gateway
        self._limiter = limiter
        self._key_secret, self._webhook_secret = key_secret, webhook_secret

    # -------------------------------------------------------------- 1. order (FR-65, FR-66)

    async def start(self, user: CurrentUser, auction_id: UUID) -> dict[str, Any]:
        """Create the gateway order for the caller's settlement, or return the existing one.
        The amount is the settlement's final price, never anything the client sends."""
        await self._limiter.hit(
            "payment_start", str(user.id), RateLimit.parse(settings.rate_limit_payment_start)
        )
        async with self._sessions() as session, session.begin():
            settlement = await self._winners_settlement(session, user, auction_id)
            if settlement.status == "paid":
                raise AppError("ALREADY_PAID", "this settlement is already paid", 409)
            if (
                settlement.final_price is None
                or settlement.final_price < settings.payment_min_amount
            ):
                raise AppError(
                    "AMOUNT_BELOW_GATEWAY_MINIMUM",
                    f"the gateway cannot charge less than {settings.payment_min_amount} paise",
                    409,
                )
            payments = PaymentRepository(session)
            existing = await payments.for_settlement(settlement.id)
            if existing is not None:
                await payments.touch(existing.id)
                return self._order_view(existing)
        order = await self._gateway_order(settlement)  # no transaction across the HTTP call
        async with self._sessions() as session, session.begin():
            payments = PaymentRepository(session)
            inserted = await payments.insert_ignore(
                settlement_id=settlement.id,
                amount=settlement.final_price,
                status="created",
                gateway_order_id=order.id,
                idempotency_key=f"order:{settlement.id}",
            )
            payment = await payments.for_settlement(settlement.id)
            assert payment is not None
            if inserted:
                await AuditLogRepository(session).add(
                    entity_type="payment",
                    entity_id=str(payment.id),
                    action="payment_created",
                    actor_id=user.id,
                    after={
                        "settlement_id": str(settlement.id),
                        "amount": payment.amount,
                        "order_id": order.id,
                    },
                )
        if inserted:
            log.info(
                "payment order created",
                extra={"fields": {"settlement_id": str(settlement.id), "order_id": order.id}},
            )
        return self._order_view(payment)

    async def _winners_settlement(
        self, session: AsyncSession, user: CurrentUser, auction_id: UUID
    ) -> Settlement:
        """FR-65: only the recorded winner; a no-sale settlement has no winner."""
        auction = await AuctionRepository(session).get(auction_id)
        if auction is None or not visible(auction, user):
            raise AppError("NOT_FOUND", "auction not found", 404)
        settlement = await SettlementRepository(session).for_auction(auction_id)
        if settlement is None:
            raise AppError("NOT_FOUND", "the auction has no settlement", 404)
        if settlement.winner_id != user.id:
            raise AppError("NOT_WINNER", "only the winning bidder can pay", 403)
        return settlement

    async def _gateway_order(self, settlement: Settlement) -> GatewayOrder:
        receipt = str(settlement.id)
        amount = settlement.final_price
        assert amount is not None
        try:
            try:
                order = await self._gateway.create_order(
                    amount=amount,
                    currency=CURRENCY,
                    receipt=receipt,
                    notes={"settlement_id": receipt, "auction_id": str(settlement.auction_id)},
                )
            except GatewayRejected as exc:
                # an order made by an earlier attempt whose commit failed: adopt it
                found = await self._gateway.find_order(receipt) if exc.duplicate_receipt else None
                if found is None:
                    log.error(
                        "gateway refused the order",
                        extra={"fields": {"settlement_id": receipt, "error": exc.description}},
                    )
                    raise _unavailable("order refused") from exc
                order = found
        except GatewayUnavailable as exc:
            raise _unavailable(str(exc)) from exc
        if order.amount != amount or order.currency != CURRENCY:
            log.critical(
                "gateway order does not match the settlement",
                extra={
                    "fields": {
                        "alert": "PAYMENT_MISMATCH",
                        "settlement_id": receipt,
                        "order_id": order.id,
                    }
                },
            )
            raise AppError("PAYMENT_MISMATCH", "the payment does not match the settlement", 409)
        return order

    def _order_view(self, payment: Payment) -> dict[str, Any]:
        return {
            "payment_id": str(payment.id),
            "settlement_id": str(payment.settlement_id),
            "status": payment.status,
            "amount": payment.amount,
            "currency": CURRENCY,
            "gateway": "razorpay",
            "key_id": self._gateway.key_id,
            "order_id": payment.gateway_order_id,
        }

    # -------------------------------------------------------------- 2a. checkout callback

    async def confirm(
        self,
        user: CurrentUser,
        auction_id: UUID,
        order_id: str,
        gateway_payment_id: str,
        signature: str,
    ) -> tuple[int, dict[str, Any]]:
        """FR-67: the signature binds the gateway payment to our stored order; it is checked
        before anything else is done with the request. Returns (HTTP status, body)."""
        await self._limiter.hit(
            "payment_confirm", str(user.id), RateLimit.parse(settings.rate_limit_payment_confirm)
        )
        async with self._sessions() as session:
            settlement = await self._winners_settlement(session, user, auction_id)
            payment = await PaymentRepository(session).for_settlement(settlement.id)
        if payment is None:
            raise AppError("NOT_FOUND", "no payment has been started", 404)
        if order_id != payment.gateway_order_id or not verify_checkout_signature(
            self._key_secret, payment.gateway_order_id, gateway_payment_id, signature
        ):
            log.warning(
                "invalid checkout signature",
                extra={"fields": {"auction_id": str(auction_id), "user_id": str(user.id)}},
            )
            raise AppError("INVALID_SIGNATURE", "the payment signature does not verify", 400)
        if settlement.status == "paid":
            return 200, {"status": "paid"}
        outcome = await self.sync_order(payment, "callback")
        if outcome == "mismatch":
            raise AppError("PAYMENT_MISMATCH", "the payment does not match the settlement", 409)
        return (202 if outcome == "pending" else 200), {"status": outcome}

    # -------------------------------------------------------------- 2b. webhook

    async def webhook(
        self, raw: bytes, signature: str | None, event_id: str | None, source_ip: str
    ) -> dict[str, str]:
        """FR-67, NFR-25: the signature over the raw bytes is verified before parsing; a bad
        or missing one changes nothing. Applied from the signed payload, no outbound call."""
        await self._limiter.hit(
            "payment_webhook", source_ip, RateLimit.parse(settings.rate_limit_payment_webhook)
        )
        if not verify_webhook_signature(self._webhook_secret, raw, signature):
            log.warning("invalid webhook signature", extra={"fields": {"source_ip": source_ip}})
            raise AppError("INVALID_SIGNATURE", "the webhook signature does not verify", 400)
        try:
            body = WebhookBody.model_validate_json(raw)
        except ValidationError:
            log.error(
                "signed webhook body not understood", extra={"fields": {"event_id": event_id}}
            )
            return {"status": "ignored"}
        entity = body.payload.payment.entity if body.payload.payment else None
        if (
            body.event not in SUCCESS_EVENTS | FAILURE_EVENTS
            or entity is None
            or not entity.order_id
        ):
            log.info(
                "webhook ignored", extra={"fields": {"event": body.event, "event_id": event_id}}
            )
            return {"status": "ignored"}
        key = event_id if event_id and _EVENT_ID.match(event_id) else f"{body.event}:{entity.id}"
        await self._apply(
            Confirmation(
                source="webhook",
                event=body.event,
                dedupe_key=f"webhook:{key}",
                gateway_payment_id=entity.id,
                order_id=entity.order_id,
                amount=entity.amount,
                currency=entity.currency,
                succeeded=body.event in SUCCESS_EVENTS,
                error_code=entity.error_code,
            )
        )
        return {"status": "ok"}

    # -------------------------------------------------------------- 2c. sync with the gateway

    async def sync_order(self, payment: Payment, source: str) -> str:
        """Read the order's payments from the gateway and apply what they show: a captured
        payment, else an authorized one after capturing it, else a failure. Returns paid,
        failed, pending (nothing to apply, or the gateway is unreachable) or mismatch."""
        try:
            found = await self._gateway.order_payments(payment.gateway_order_id)
            captured = [p for p in found if p.status == "captured"]
            authorized = [p for p in found if p.status == "authorized"]
            if not captured and authorized:
                captured = [await self._capture(authorized[0], payment)]
        except (GatewayUnavailable, GatewayRejected) as exc:
            log.warning(
                "payment sync deferred",
                extra={"fields": {"payment_id": str(payment.id), "error": repr(exc)}},
            )
            return "pending"
        outcome = "pending"
        for p in captured:  # more than one would be a duplicate charge: each is reported
            outcome = await self._apply(self._from_gateway(source, p, payment, succeeded=True))
        if captured:
            return outcome
        failed = [p for p in found if p.status == "failed"]
        if failed:
            return await self._apply(self._from_gateway(source, failed[-1], payment, False))
        return "pending"

    async def _capture(self, authorized: GatewayPayment, payment: Payment) -> GatewayPayment:
        try:
            return await self._gateway.capture(authorized.id, payment.amount, CURRENCY)
        except GatewayRejected as exc:
            if not exc.already_captured:
                raise
            refreshed = await self._gateway.order_payments(payment.gateway_order_id)
            return next(p for p in refreshed if p.id == authorized.id)

    @staticmethod
    def _from_gateway(
        source: str, p: GatewayPayment, payment: Payment, succeeded: bool
    ) -> Confirmation:
        outcome = "succeeded" if succeeded else "failed"
        return Confirmation(
            source=source,
            event="payment.captured" if succeeded else "payment.failed",
            dedupe_key=f"{source}:{p.id}:{outcome}",
            gateway_payment_id=p.id,
            order_id=p.order_id or payment.gateway_order_id,
            amount=p.amount,
            currency=p.currency,
            succeeded=succeeded and p.status == "captured",
            error_code=p.error_code,
        )

    # -------------------------------------------------------------- the one transaction

    async def _apply(self, c: Confirmation) -> str:
        fields = {
            "source": c.source,
            "order_id": c.order_id,
            "gateway_payment_id": c.gateway_payment_id,
        }
        created: list[Notification] = []
        async with self._sessions() as session, session.begin():
            payments = PaymentRepository(session)
            payment = await payments.lock_by_order(c.order_id)
            if payment is None:
                log.warning("confirmation for an unknown order", extra={"fields": fields})
                return "unknown"
            fields["payment_id"] = str(payment.id)
            settlement = await SettlementRepository(session).get(payment.settlement_id)
            assert settlement is not None
            if c.succeeded:
                if (
                    c.amount != payment.amount
                    or payment.amount != settlement.final_price
                    or c.currency != CURRENCY
                ):
                    log.critical(
                        "payment does not match its settlement; not applied",
                        extra={"fields": fields | {"alert": "PAYMENT_MISMATCH"}},
                    )
                    return "mismatch"
                if payment.status == "succeeded":
                    if payment.gateway_payment_id != c.gateway_payment_id:
                        log.critical(
                            "second successful charge on a paid settlement; refund manually",
                            extra={"fields": fields | {"alert": "PAYMENT_DUPLICATE_CHARGE"}},
                        )
                    return "paid"
                if not await self._record(session, payment, c, "succeeded"):
                    return "paid"
                created = await self._succeeded(session, payment, settlement, c)
                outcome = "paid"
            else:
                if payment.status == "succeeded":
                    return "paid"  # a failed attempt never undoes a capture
                events = PaymentEventRepository(session)
                if await events.failure_applied(payment.id, c.gateway_payment_id):
                    return "failed"
                if not await self._record(session, payment, c, "failed"):
                    return "failed"
                created = await self._failed(session, payment, settlement, c)
                outcome = "failed"
        log.info("payment outcome applied", extra={"fields": fields | {"outcome": outcome}})
        await live_events.publish_notifications(self._redis, created)
        return outcome

    @staticmethod
    async def _record(
        session: AsyncSession, payment: Payment, c: Confirmation, outcome: str
    ) -> bool:
        return await PaymentEventRepository(session).add(
            payment_id=payment.id,
            dedupe_key=c.dedupe_key,
            source=c.source,
            event=c.event,
            gateway_payment_id=c.gateway_payment_id,
            outcome=outcome,
        )

    async def _succeeded(
        self, session: AsyncSession, payment: Payment, settlement: Settlement, c: Confirmation
    ) -> list[Notification]:
        # read before the update: the ORM update also refreshes the loaded row (FR-77)
        before = {"status": payment.status}
        await PaymentRepository(session).set_status(payment.id, "succeeded", c.gateway_payment_id)
        if not await SettlementRepository(session).mark_paid(settlement.id):
            raise RuntimeError(f"settlement {settlement.id} is not awaiting payment")
        audit = AuditLogRepository(session)
        await audit.add(
            entity_type="payment",
            entity_id=str(payment.id),
            action="payment_succeeded",
            before=before,
            after={
                "status": "succeeded",
                "gateway_payment_id": c.gateway_payment_id,
                "source": c.source,
                "event": c.event,
                "dedupe_key": c.dedupe_key,
            },
        )
        await audit.add(  # FR-64
            entity_type="settlement",
            entity_id=str(settlement.id),
            action="status_changed",
            before={"status": "pending_payment"},
            after={"status": "paid", "payment_id": str(payment.id)},
        )
        auction = await AuctionRepository(session).get(settlement.auction_id)
        assert auction is not None and settlement.winner_id is not None
        payload = {
            "auction_id": str(auction.id),
            "title": auction.title,
            "amount": payment.amount,
            "settlement_id": str(settlement.id),
        }
        return await NotificationRepository(session).add_many(
            [
                {
                    "user_id": user,
                    "auction_id": auction.id,
                    "type": "payment_received",
                    "payload": payload,
                    "dedupe_key": f"pay:{settlement.id}:payment_received:{user}",
                }
                for user in (settlement.winner_id, auction.seller_id)  # both parties (FR-68)
            ]
        )

    async def _failed(
        self, session: AsyncSession, payment: Payment, settlement: Settlement, c: Confirmation
    ) -> list[Notification]:
        before = {"status": payment.status}  # before the update refreshes the loaded row
        await PaymentRepository(session).set_status(payment.id, "failed")
        await AuditLogRepository(session).add(
            entity_type="payment",
            entity_id=str(payment.id),
            action="payment_failed",
            before=before,
            after={
                "status": "failed",
                "gateway_payment_id": c.gateway_payment_id,
                "error_code": c.error_code,
                "source": c.source,
                "event": c.event,
                "dedupe_key": c.dedupe_key,
            },
        )
        auction = await AuctionRepository(session).get(settlement.auction_id)
        assert auction is not None and settlement.winner_id is not None
        return await NotificationRepository(session).add_many(
            [
                {
                    "user_id": settlement.winner_id,
                    "auction_id": auction.id,
                    "type": "payment_failed",
                    "payload": {
                        "auction_id": str(auction.id),
                        "title": auction.title,
                        "amount": payment.amount,
                        "error_code": c.error_code,
                    },
                    "dedupe_key": f"pay:{payment.id}:failed:{c.gateway_payment_id}",
                }
            ]
        )

    # -------------------------------------------------------------- 3. reconciliation (FR-69)

    async def reconcile(self) -> dict[str, int]:
        async with self._sessions() as session:
            due = await PaymentRepository(session).due_for_reconcile(
                settings.payment_reconcile_after_s,
                settings.payment_reconcile_lookback_s,
                settings.payment_reconcile_batch,
            )
        counts: dict[str, int] = {}
        for payment in due:
            outcome = await self.sync_order(payment, "reconcile")
            counts[outcome] = counts.get(outcome, 0) + 1
            async with self._sessions() as session, session.begin():
                await PaymentRepository(session).mark_reconciled(payment.id)
        return counts
