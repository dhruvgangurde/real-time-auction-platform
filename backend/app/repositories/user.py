from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import func, select, update

from app.models import User, UserRole
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository):
    async def get(self, user_id: UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        """Case-insensitive lookup (FR-1); the unique index is on lower(email)."""
        stmt = select(User).where(func.lower(User.email) == email.lower())
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def create(
        self, email: str, password_hash: str, full_name: str, roles: Iterable[str]
    ) -> User:
        user = User(
            email=email,
            password_hash=password_hash,
            full_name=full_name,
            status="pending_verification",
        )
        self.session.add(user)
        await self.session.flush()
        for role in roles:
            self.session.add(UserRole(user_id=user.id, role=role))
        await self.session.flush()
        return user

    async def roles(self, user_id: UUID) -> tuple[str, ...]:
        stmt = select(UserRole.role).where(UserRole.user_id == user_id).order_by(UserRole.role)
        return tuple((await self.session.execute(stmt)).scalars())

    async def set_status(self, user_id: UUID, status: str) -> None:
        await self.session.execute(update(User).where(User.id == user_id).values(status=status))

    async def set_password_hash(self, user_id: UUID, password_hash: str) -> None:
        await self.session.execute(
            update(User).where(User.id == user_id).values(password_hash=password_hash)
        )

    async def update_profile(
        self, user_id: UUID, full_name: str | None, avatar_url: str | None, clear_avatar: bool
    ) -> None:
        """Only name and avatar are self-service editable (FR-9)."""
        values: dict[str, object] = {}
        if full_name is not None:
            values["full_name"] = full_name
        if clear_avatar:
            values["avatar_url"] = None
        elif avatar_url is not None:
            values["avatar_url"] = avatar_url
        if values:
            await self.session.execute(update(User).where(User.id == user_id).values(**values))
