from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from app.core.config import settings


def create_redis(url: str) -> Redis:
    """Short timeouts and one retry so the bid path fails closed fast (FR-42, NFR-9).

    A retried script call is safe: the request key makes arbitration idempotent.
    """
    return Redis.from_url(
        url,
        decode_responses=True,
        socket_timeout=settings.redis_timeout_s,
        socket_connect_timeout=settings.redis_timeout_s,
        retry=Retry(NoBackoff(), 1),
    )


redis: Redis = create_redis(settings.redis_url)
