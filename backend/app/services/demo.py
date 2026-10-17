"""Demo mode (brief 12.3, 12.4): what the landing page reads about the demo.

The hero loop is deferred (B2 Q9), so there is never a hero lot; the hero replays the most
recent verified "Try to break it" run instead (brief 8.1 L324)."""

from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.demo import DemoRunRepository


class DemoService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], redis: Redis) -> None:
        self._sessions, self._redis = sessions, redis

    async def overview(self) -> dict[str, Any]:
        async with self._sessions() as session:
            replay = await DemoRunRepository(session).latest_verified_auction("stress")
        return {
            "enabled": True,  # this is only ever answered while demo mode is on
            "auction_id": None,
            "next_start_ms": None,
            "last_closed_auction_id": str(replay) if replay else None,
        }
