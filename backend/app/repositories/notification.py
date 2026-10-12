from typing import Any

from sqlalchemy.dialects.postgresql import insert

from app.models import Notification
from app.repositories.base import BaseRepository


class NotificationRepository(BaseRepository):
    async def add_many(self, rows: list[dict[str, Any]]) -> None:
        """In-app notifications; an existing dedupe_key makes a repeat a no-op (DC-5)."""
        if rows:
            await self.session.execute(
                insert(Notification)
                .values(rows)
                .on_conflict_do_nothing(index_elements=["dedupe_key"])
            )
