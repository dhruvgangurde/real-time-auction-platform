from typing import Any
from uuid import UUID

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
