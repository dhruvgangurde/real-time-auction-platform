from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import exists, func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.models import Payment, PaymentEvent, Settlement
from app.repositories.base import BaseRepository


class PaymentRepository(BaseRepository):
    async def for_settlement(self, settlement_id: UUID) -> Payment | None:
        stmt = select(Payment).where(Payment.settlement_id == settlement_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def insert_ignore(self, **values: Any) -> bool:
        """One row per settlement: a concurrent or retried start finds the first one (FR-66)."""
        stmt = (
            insert(Payment)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(Payment.id)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

    async def touch(self, payment_id: UUID) -> None:
        """Payment activity: keeps the order inside the reconcile lookback (FR-69)."""
        await self.session.execute(
            update(Payment).where(Payment.id == payment_id).values(updated_at=func.now())
        )

    async def lock_by_order(self, gateway_order_id: str) -> Payment | None:
        """Every confirmation source serialises on this row lock (FR-67)."""
        stmt = select(Payment).where(Payment.gateway_order_id == gateway_order_id).with_for_update()
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def set_status(
        self, payment_id: UUID, status: str, gateway_payment_id: str | None = None
    ) -> None:
        values: dict[str, Any] = {"status": status, "updated_at": func.now()}
        if gateway_payment_id is not None:
            values["gateway_payment_id"] = gateway_payment_id
        await self.session.execute(update(Payment).where(Payment.id == payment_id).values(values))

    async def due_for_reconcile(self, after_s: int, lookback_s: int, limit: int) -> list[Payment]:
        """Unpaid orders of unpaid settlements with recent activity, least recently checked
        first, so a long backlog cannot starve newer orders (FR-69)."""
        now = func.now()
        stmt = (
            select(Payment)
            .join(Settlement, Settlement.id == Payment.settlement_id)
            .where(
                Payment.status.in_(("created", "failed")),
                Settlement.status == "pending_payment",
                Payment.updated_at <= now - timedelta(seconds=after_s),
                Payment.updated_at > now - timedelta(seconds=lookback_s),
            )
            .order_by(Payment.reconciled_at.asc().nulls_first(), Payment.created_at)
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def mark_reconciled(self, payment_id: UUID) -> None:
        await self.session.execute(
            update(Payment).where(Payment.id == payment_id).values(reconciled_at=func.now())
        )


class PaymentEventRepository(BaseRepository):
    async def add(self, **values: Any) -> bool:
        """False when this dedupe_key was already applied (FR-67, DC-5)."""
        stmt = (
            insert(PaymentEvent)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["dedupe_key"])
            .returning(PaymentEvent.id)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

    async def failure_applied(self, payment_id: UUID, gateway_payment_id: str) -> bool:
        """Whether this gateway payment's failure was already applied, from any source."""
        stmt = select(
            exists().where(
                PaymentEvent.payment_id == payment_id,
                PaymentEvent.gateway_payment_id == gateway_payment_id,
                PaymentEvent.outcome == "failed",
            )
        )
        return bool((await self.session.execute(stmt)).scalar_one())
