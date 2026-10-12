from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.models import AuditLog
from app.repositories.base import BaseRepository


class AuditLogRepository(BaseRepository):
    async def add(
        self,
        *,
        entity_type: str,
        entity_id: str,
        action: str,
        actor_id: UUID | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> None:
        """Append an audit entry in the caller's transaction (FR-77, FR-78).

        actor_id None means the system (scheduler or worker).
        """
        self.session.add(
            AuditLog(
                actor_id=actor_id,
                entity_type=entity_type,
                entity_id=entity_id,
                action=action,
                before=before,
                after=after,
                reason=reason,
            )
        )
        await self.session.flush()

    async def entity_ids_for_action(self, action: str, since: datetime) -> list[UUID]:
        """Entity ids (as UUIDs) of recent entries with this action, for the denylist resync."""
        stmt = select(AuditLog.entity_id).where(
            AuditLog.action == action, AuditLog.created_at >= since
        )
        return [UUID(e) for e in (await self.session.execute(stmt)).scalars()]

    async def query(
        self,
        *,
        actor_id: UUID | None,
        entity_type: str | None,
        entity_id: str | None,
        since: datetime | None,
        until: datetime | None,
        before_id: int | None,
        limit: int,
    ) -> list[AuditLog]:
        """Administrator audit search (FR-80), newest first, keyset on id."""
        stmt = select(AuditLog)
        if actor_id is not None:
            stmt = stmt.where(AuditLog.actor_id == actor_id)
        if entity_type is not None:
            stmt = stmt.where(AuditLog.entity_type == entity_type)
        if entity_id is not None:
            stmt = stmt.where(AuditLog.entity_id == entity_id)
        if since is not None:
            stmt = stmt.where(AuditLog.created_at >= since)
        if until is not None:
            stmt = stmt.where(AuditLog.created_at < until)
        if before_id is not None:
            stmt = stmt.where(AuditLog.id < before_id)
        stmt = stmt.order_by(AuditLog.id.desc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars())
