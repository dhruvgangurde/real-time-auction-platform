from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EmailVerificationToken, PasswordResetToken
from app.repositories.base import BaseRepository

TokenModel = type[EmailVerificationToken] | type[PasswordResetToken]


class TokenRepository(BaseRepository):
    """Single-use, time-limited tokens stored only as hashes (FR-3, FR-8)."""

    def __init__(self, session: AsyncSession, model: TokenModel) -> None:
        super().__init__(session)
        self._model = model

    async def create(self, user_id: UUID, token_hash: str, expires_at: datetime) -> None:
        self.session.add(self._model(user_id=user_id, token_hash=token_hash, expires_at=expires_at))
        await self.session.flush()

    async def find(self, token_hash: str) -> tuple[UUID, datetime, datetime | None] | None:
        """(user_id, expires_at, used_at) without changing anything: a cheap pre-check."""
        stmt = select(self._model.user_id, self._model.expires_at, self._model.used_at).where(
            self._model.token_hash == token_hash
        )
        row = (await self.session.execute(stmt)).first()
        return (row.user_id, row.expires_at, row.used_at) if row else None

    async def consume(self, token_hash: str, now: datetime) -> tuple[UUID, datetime] | None:
        """Mark the token used and return (user_id, expires_at); None if unknown or already used.

        One UPDATE with `used_at IS NULL` makes single use atomic under concurrent presentation.
        """
        result = await self.session.execute(
            update(self._model)
            .where(self._model.token_hash == token_hash, self._model.used_at.is_(None))
            .values(used_at=now)
            .returning(self._model.user_id, self._model.expires_at)
        )
        row = result.first()
        return (row.user_id, row.expires_at) if row else None
