from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import delete, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.models import User, UserRole
from app.repositories.base import BaseRepository

# fixed advisory-lock key: serialises every change that can remove an active administrator
ADMIN_SET_LOCK = 0x41444D494E5F5345  # "ADMIN_SE"


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

    async def add_role(self, user_id: UUID, role: str) -> bool:
        """False when the user already holds it."""
        stmt = (
            insert(UserRole)
            .values(user_id=user_id, role=role)
            .on_conflict_do_nothing(index_elements=["user_id", "role"])
            .returning(UserRole.role)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

    async def remove_role(self, user_id: UUID, role: str) -> bool:
        """False when the user did not hold it."""
        stmt = (
            delete(UserRole)
            .where(UserRole.user_id == user_id, UserRole.role == role)
            .returning(UserRole.role)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

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

    # ---------------------------------------------------------------- administration (FR-76)

    async def search(
        self,
        *,
        query: str | None,
        role: str | None,
        status: str | None,
        include_demo: bool,
        limit: int,
        offset: int,
    ) -> list[User]:
        """Free text over email and name (LIKE wildcards in the text match literally), role and
        status filters; newest first. Demo accounts only when asked (brief 12.3)."""
        stmt = select(User)
        if not include_demo:
            stmt = stmt.where(User.is_demo.is_(False))
        if query:
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            stmt = stmt.where(
                or_(
                    User.email.ilike(pattern, escape="\\"),
                    User.full_name.ilike(pattern, escape="\\"),
                )
            )
        if role is not None:
            stmt = stmt.where(exists().where(UserRole.user_id == User.id, UserRole.role == role))
        if status is not None:
            stmt = stmt.where(User.status == status)
        stmt = stmt.order_by(User.created_at.desc(), User.id.desc()).limit(limit).offset(offset)
        return list((await self.session.execute(stmt)).scalars())

    async def roles_for(self, user_ids: list[UUID]) -> dict[UUID, list[str]]:
        stmt = (
            select(UserRole.user_id, UserRole.role)
            .where(UserRole.user_id.in_(user_ids))
            .order_by(UserRole.role)
        )
        roles: dict[UUID, list[str]] = {u: [] for u in user_ids}
        for r in (await self.session.execute(stmt)).all():
            roles[r.user_id].append(r.role)
        return roles

    async def suspended_ids(self, user_ids: list[UUID]) -> list[UUID]:
        """Those of these users who are suspended now (denylist resync)."""
        if not user_ids:
            return []
        stmt = select(User.id).where(User.id.in_(user_ids), User.status == "suspended")
        return list((await self.session.execute(stmt)).scalars())

    async def get_many(self, user_ids: list[UUID]) -> dict[UUID, User]:
        stmt = select(User).where(User.id.in_(user_ids))
        return {u.id: u for u in (await self.session.execute(stmt)).scalars()}

    # ---------------------------------------------------------------- administrators (FR-75)

    async def lock_admin_set(self) -> None:
        """Held for the transaction: two administrators suspending or demoting each other at
        once cannot both pass the last-admin check."""
        await self.session.execute(select(func.pg_advisory_xact_lock(ADMIN_SET_LOCK)))

    async def other_active_admins(self, user_id: UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(User)
            .join(UserRole, UserRole.user_id == User.id)
            .where(UserRole.role == "admin", User.status == "active", User.id != user_id)
        )
        return (await self.session.execute(stmt)).scalar_one()
