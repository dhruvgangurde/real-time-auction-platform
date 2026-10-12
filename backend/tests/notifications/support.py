"""Notification fixtures: a recording email sender that can fail or hold a send, the service
wired to it, and a clean email queue (the dispatcher looks at every pending row)."""

import asyncio
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.notifications import NotificationService


class Outbox:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []
        self.failures = 0  # the next N sends raise
        self.gate: asyncio.Event | None = None  # when set, a send waits for it

    async def send(self, to: str, subject: str, body: str) -> None:
        if self.gate is not None:
            await self.gate.wait()
        if self.failures:
            self.failures -= 1
            raise ConnectionError("smtp unreachable")
        self.sent.append((to, subject, body))


@pytest.fixture
def outbox() -> Outbox:
    return Outbox()


@pytest.fixture
def notification_service(
    sessions: async_sessionmaker[AsyncSession], redis: Redis, outbox: Outbox
) -> NotificationService:
    return NotificationService(sessions, redis, outbox)


@pytest.fixture(autouse=True)
async def quiet_email(db: asyncpg.Connection) -> None:
    """Rows left by other packages' tests are not this test's to send."""
    await db.execute(
        "UPDATE notifications SET email_status = 'skipped' WHERE email_status = 'pending'"
    )


async def add_notification(
    db: asyncpg.Connection,
    user_id: UUID,
    auction_id: UUID | None,
    kind: str = "outbid",
    *,
    age_s: float = 0,
    payload: dict[str, Any] | None = None,
) -> int:
    import json

    return await db.fetchval(
        "INSERT INTO notifications (user_id, auction_id, type, payload, dedupe_key, created_at) "
        "VALUES ($1, $2, $3, $4::jsonb, $5, now() - make_interval(secs => $6)) RETURNING id",
        user_id,
        auction_id,
        kind,
        json.dumps(
            payload
            or {"auction_id": str(auction_id), "title": "Brass telescope", "current_price": 12_300}
        ),
        f"test:{uuid4()}",
        float(age_s),
    )


async def email_rows(db: asyncpg.Connection, ids: list[int]) -> list[asyncpg.Record]:
    return await db.fetch(
        "SELECT id, email_status, email_attempts, email_next_at, emailed_at, "
        "now() AS now FROM notifications WHERE id = ANY($1::bigint[]) ORDER BY id",
        ids,
    )


def seconds_until(row: asyncpg.Record) -> float:
    next_at: datetime = row["email_next_at"]
    return (next_at - row["now"]).total_seconds()
