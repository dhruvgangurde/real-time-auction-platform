import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Settlement(Base):
    """Exactly one per auction (FR-61, OR-3). idempotency_key makes creation retry-safe (DC-5)."""

    __tablename__ = "settlements"
    __table_args__ = (
        CheckConstraint(
            # demo: a demo lot's sale, never payable (B2 Q1)
            "status IN ('pending_payment', 'paid', 'no_sale', 'demo')",
            name="status",
        ),
        CheckConstraint(
            "(status = 'no_sale' AND winner_id IS NULL AND final_price IS NULL) OR "
            "(status <> 'no_sale' AND winner_id IS NOT NULL AND final_price IS NOT NULL)",
            name="winner_matches_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    auction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auctions.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    winner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    final_price: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Payment(Base):
    """One row per Razorpay order (idempotency_key 'order:{settlement_id}'); gateway_payment_id
    is the captured payment. At most one succeeded payment per settlement (FR-66, FR-67, DC-5).
    Status: created -> failed | succeeded, failed -> succeeded; succeeded is terminal."""

    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("status IN ('created', 'succeeded', 'failed')", name="status"),
        CheckConstraint("amount > 0", name="amount_pos"),
        Index(
            "uq_payments_settlement_succeeded",
            "settlement_id",
            unique=True,
            postgresql_where=text("status = 'succeeded'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    settlement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("settlements.id", ondelete="RESTRICT"), nullable=False
    )
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    gateway_payment_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    gateway_order_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PaymentEvent(Base):
    """Every applied terminal confirmation, once: the dedupe_key is the event id of a webhook,
    the payment id of a checkout callback, or payment id and status for the reconciler
    (FR-67, DC-5). Append-only for the application role."""

    __tablename__ = "payment_events"
    __table_args__ = (
        CheckConstraint("source IN ('callback', 'webhook', 'reconcile')", name="source"),
        CheckConstraint("outcome IN ('succeeded', 'failed')", name="outcome"),
        Index("ix_payment_events_payment_id", "payment_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    payment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("payments.id", ondelete="RESTRICT")
    )
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    gateway_payment_id: Mapped[str | None] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
