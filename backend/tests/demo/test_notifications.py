"""Nothing demo-related reaches anyone's notifications or email (B2 Q2): a demo lot writes no
close set, no outbid rows and no ending-soon rows, for bots and people alike; the outbid task is
not even enqueued for a demo lot (Q13); and the email dispatcher skips demo accounts as a
backstop. Live room events still flow (bid_placed and the rest are the demo)."""

from datetime import timedelta
from uuid import UUID

import asyncpg
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services import live_events
from app.services.bidding import BidService
from app.services.closing import Closer, Outcome
from app.services.notifications import NotificationService
from tests.bidding.conftest import AuctionFactory, PersistRecorder
from tests.closing.conftest import Ended
from tests.db.conftest import make_auction, make_user
from tests.notifications.support import Outbox, add_notification, email_rows


async def _demo(db: asyncpg.Connection, auction_id: UUID) -> UUID:
    await db.execute("UPDATE auctions SET is_demo = true WHERE id = $1", auction_id)
    return auction_id


async def _rows(db: asyncpg.Connection, auction_id: UUID) -> list[str]:
    rows = await db.fetch(
        "SELECT type FROM notifications WHERE auction_id = $1 ORDER BY id", auction_id
    )
    return [r["type"] for r in rows]


async def test_a_demo_lot_closes_without_a_notification_set(
    ended: Ended, db: asyncpg.Connection, redis: Redis, sessions: async_sessionmaker[AsyncSession]
) -> None:
    person, bot = await make_user(db), await make_user(db)
    await db.execute("UPDATE users SET is_demo = true WHERE id = $1", bot)
    lot = await _demo(db, await ended(bids=[(bot, 10_000), (person, 10_500)]))
    real = await ended(bids=[(bot, 10_000), (person, 10_500)])
    closer = Closer(redis, sessions)
    assert await closer.close(lot) == Outcome.SETTLED
    assert await closer.close(real) == Outcome.SETTLED
    assert await _rows(db, lot) == []  # neither the person who won nor the bot who lost
    assert sorted(await _rows(db, real)) == [
        "auction_lost",
        "auction_sold",
        "auction_won",
        "payment_due",
    ]


async def test_no_outbid_rows_for_a_demo_lot(
    notification_service: NotificationService, db: asyncpg.Connection
) -> None:
    lot = await make_auction(db, is_demo=True, status="live")
    person = await make_user(db)
    assert await notification_service.record_outbid(lot, [person], 2, 10_500) == 0
    assert await _rows(db, lot) == []


async def test_no_ending_soon_for_a_watched_demo_lot(
    notification_service: NotificationService, db: asyncpg.Connection
) -> None:
    now = await db.fetchval("SELECT now()")
    watcher = await make_user(db)
    lots = {}
    for demo in (True, False):
        end = now + timedelta(seconds=90)
        lots[demo] = await make_auction(
            db,
            is_demo=demo,
            status="live",
            start_time=now - timedelta(minutes=5),
            end_time=end,
            original_end_time=end,
        )
        await db.execute(
            "INSERT INTO watchlist (user_id, auction_id) VALUES ($1, $2)", watcher, lots[demo]
        )
    await notification_service.notify_ending_soon()
    assert await _rows(db, lots[True]) == []
    assert await _rows(db, lots[False]) == ["ending_soon"]


async def test_the_dispatcher_never_emails_a_demo_account(
    notification_service: NotificationService, outbox: Outbox, db: asyncpg.Connection
) -> None:
    bot = await make_user(db, email="bot-backstop@demo.invalid")
    await db.execute("UPDATE users SET is_demo = true, status = 'active' WHERE id = $1", bot)
    row = await add_notification(db, bot, await make_auction(db), age_s=90)
    await notification_service.dispatch_emails()
    assert [r["email_status"] for r in await email_rows(db, [row])] == ["skipped"]
    assert [to for to, *_ in outbox.sent if to.endswith("@demo.invalid")] == []


async def test_the_outbid_task_is_not_even_enqueued_for_a_demo_lot(
    auction: AuctionFactory, redis: Redis, sessions: async_sessionmaker[AsyncSession], db
) -> None:
    """Q13: the bid path knows the lot is a demo (BidService facts) and skips the outbid
    recorder at the source, so a stress run does not flood Celery with tasks that write
    nothing. A real lot still records the displaced bidder."""
    enqueued: list[tuple[UUID, list[UUID]]] = []
    service = BidService(
        redis, sessions, PersistRecorder(), lambda a, users, *_: enqueued.append((a, users))
    )
    first, second = await make_user(db), await make_user(db)
    for demo in (True, False):
        lot = await auction()
        if demo:
            await _demo(db, lot)  # before the first bid, which reads and caches the facts
        for bidder, amount in ((first, 10_000), (second, 10_500)):
            result = await service.place_bid(lot, bidder, amount, f"r{amount}")
            assert result.accepted and result.demo is demo
            await live_events.publish_bid_events(redis, lot, bidder, result, service.record_outbid)
        assert [users for a, users in enqueued if a == lot] == ([] if demo else [[first]])
