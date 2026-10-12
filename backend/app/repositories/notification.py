from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased

from app.models import Notification, NotificationPreference
from app.repositories.base import BaseRepository


class NotificationRepository(BaseRepository):
    async def add_many(self, rows: list[dict[str, Any]]) -> list[Notification]:
        """In-app notifications; an existing dedupe_key makes a repeat a no-op (DC-5).
        Returns the rows this call inserted, for the live push (FR-71)."""
        if not rows:
            return []
        stmt = (
            insert(Notification)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["dedupe_key"])
            .returning(Notification)
        )
        return list((await self.session.execute(stmt)).scalars())

    # ---------------------------------------------------------------- the user's view (FR-74)

    async def page(
        self, user_id: UUID, *, unread: bool, before: int | None, limit: int
    ) -> list[Notification]:
        """Most recent first, keyset on id (identity order is creation order)."""
        stmt = select(Notification).where(Notification.user_id == user_id)
        if unread:
            stmt = stmt.where(Notification.read_at.is_(None))
        if before is not None:
            stmt = stmt.where(Notification.id < before)
        stmt = stmt.order_by(Notification.id.desc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars())

    async def mark_read(self, user_id: UUID, notification_id: int) -> bool:
        """False when the notification is not this user's (or does not exist)."""
        stmt = (
            update(Notification)
            .where(Notification.id == notification_id, Notification.user_id == user_id)
            .values(read_at=func.coalesce(Notification.read_at, func.now()))
            .returning(Notification.id)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

    async def mark_all_read(self, user_id: UUID) -> int:
        stmt = (
            update(Notification)
            .where(Notification.user_id == user_id, Notification.read_at.is_(None))
            .values(read_at=func.now())
        )
        return (await self.session.execute(stmt)).rowcount

    async def email_preferences(self, user_ids: list[UUID]) -> dict[tuple[UUID, str], bool]:
        """Stored choices only; a type without a row is enabled."""
        stmt = select(NotificationPreference).where(NotificationPreference.user_id.in_(user_ids))
        rows = (await self.session.execute(stmt)).scalars()
        return {(p.user_id, p.type): p.email_enabled for p in rows}

    async def set_email_preferences(self, user_id: UUID, choices: dict[str, bool]) -> None:
        for kind, enabled in choices.items():
            stmt = (
                insert(NotificationPreference)
                .values(user_id=user_id, type=kind, email_enabled=enabled)
                .on_conflict_do_update(
                    index_elements=["user_id", "type"], set_={"email_enabled": enabled}
                )
            )
            await self.session.execute(stmt)

    # ---------------------------------------------------------------- email delivery (FR-72)

    async def claim_email(self, window_s: int, lease_s: int, limit: int) -> list[Notification]:
        """Claim pending rows of the groups that are due, with a lease, and commit before any
        sending. A (user, type, auction) group is due once its oldest pending row is older than
        the window, so rapid repeats wait and leave together. Concurrent dispatchers skip
        locked rows and leased rows."""
        older = aliased(Notification)
        due = (
            select(older.id)
            .where(
                older.user_id == Notification.user_id,
                older.type == Notification.type,
                older.auction_id.is_not_distinct_from(Notification.auction_id),
                older.email_status == "pending",
                older.created_at <= func.now() - timedelta(seconds=window_s),
            )
            .exists()
        )
        claimable = (
            select(Notification.id)
            .where(
                Notification.email_status == "pending",
                or_(Notification.email_next_at.is_(None), Notification.email_next_at <= func.now()),
                due,
            )
            .order_by(
                Notification.user_id, Notification.type, Notification.auction_id, Notification.id
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        stmt = (
            update(Notification)
            .where(Notification.id.in_(claimable.scalar_subquery()))
            .values(
                email_attempts=Notification.email_attempts + 1,
                email_next_at=func.now() + timedelta(seconds=lease_s),
            )
            .returning(Notification)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def finish_email(self, ids: list[int], status: str) -> None:
        """sent, skipped or failed: the group leaves the queue."""
        values: dict[str, Any] = {"email_status": status, "email_next_at": None}
        if status == "sent":
            values["emailed_at"] = func.now()
        await self.session.execute(
            update(Notification).where(Notification.id.in_(ids)).values(values)
        )

    async def retry_email(self, ids: list[int], delay_s: int) -> None:
        await self.session.execute(
            update(Notification)
            .where(Notification.id.in_(ids))
            .values(email_next_at=func.now() + timedelta(seconds=delay_s))
        )
