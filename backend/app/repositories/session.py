from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update

from app.models import Session as SessionRow
from app.repositories.base import BaseRepository


class SessionRepository(BaseRepository):
    """Refresh sessions: only the hash of the refresh token is stored (NFR-17)."""

    async def create(self, user_id: UUID, token_hash: str, expires_at: datetime) -> UUID:
        row = SessionRow(user_id=user_id, refresh_token_hash=token_hash, expires_at=expires_at)
        self.session.add(row)
        await self.session.flush()
        return row.id

    async def get(self, session_id: UUID) -> SessionRow | None:
        return await self.session.get(SessionRow, session_id)

    async def get_by_token_hash(self, token_hash: str) -> SessionRow | None:
        stmt = select(SessionRow).where(SessionRow.refresh_token_hash == token_hash)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def rotate(self, session_id: UUID, new_hash: str, expires_at: datetime) -> None:
        """Replace the refresh token on the same session; the old one stops working (NFR-17)."""
        await self.session.execute(
            update(SessionRow)
            .where(SessionRow.id == session_id)
            .values(refresh_token_hash=new_hash, expires_at=expires_at)
        )

    async def revoke(self, session_id: UUID, now: datetime) -> bool:
        result = await self.session.execute(
            update(SessionRow)
            .where(SessionRow.id == session_id, SessionRow.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        return bool(result.rowcount)

    async def revoke_all_for_user(self, user_id: UUID, now: datetime) -> list[UUID]:
        """Revoke every live session; returns their ids for the denylist (FR-6, FR-8)."""
        result = await self.session.execute(
            update(SessionRow)
            .where(SessionRow.user_id == user_id, SessionRow.revoked_at.is_(None))
            .values(revoked_at=now)
            .returning(SessionRow.id)
        )
        return list(result.scalars())

    async def revoked_since(self, since: datetime) -> list[UUID]:
        """Sessions revoked recently, for the denylist resync safety net."""
        stmt = select(SessionRow.id).where(SessionRow.revoked_at >= since)
        return list((await self.session.execute(stmt)).scalars())
