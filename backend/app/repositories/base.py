from sqlalchemy.ext.asyncio import AsyncSession


class BaseRepository:
    """All queries live in repositories (DC-8). Methods are added when a service needs them."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
