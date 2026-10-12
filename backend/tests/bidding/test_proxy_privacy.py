"""A proxy maximum is disclosed to nobody but its owner (FR-51, BR-7, NFR-27).

Under FR-49 the final price reveals the runner-up's level by construction (second-price
property), and the runner-up's record bid at its own maximum discloses nothing beyond that.
What BR-7 protects is the leading maximum, which is never bid at. This test arms a leading
maximum with a distinctive value and asserts it appears nowhere except in its owner's own
result: not in other bidders' results, not in any log line, not in audit_log.
"""

import logging

import asyncpg
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.bidding import BidService
from app.services.ledger import persist_outbox
from app.services.reconciliation import reconcile_auction
from tests.bidding.conftest import AuctionFactory

SECRET_MAX = 777_001  # alice's leading maximum: never reached, must never leak
RUNNER_UP_MAX = 555_001  # bob's maximum: becomes a public bid amount under FR-49


async def test_leading_maximum_appears_only_in_owner_result(
    service: BidService,
    auction: AuctionFactory,
    new_user,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    db: asyncpg.Connection,
    caplog,
) -> None:
    a = await auction(start_price=10_000, min_increment=100)
    alice, bob, carol = await new_user(), await new_user(), await new_user()
    with caplog.at_level(logging.DEBUG):
        own = await service.set_proxy(a, alice, SECRET_MAX, "alice-arm")
        others = [
            await service.set_proxy(a, bob, RUNNER_UP_MAX, "bob-arm"),
            await service.place_bid(a, carol, 600_000, "carol-bid"),
            await service.place_bid(a, carol, 600_000, "carol-bid"),  # idempotent replay
            await service.set_proxy(a, bob, 700_000, "bob-raise"),
            await service.place_bid(a, bob, 760_000, "bob-manual"),
            await service.place_bid(a, carol, 800_000, "carol-wins"),
        ]
        await persist_outbox(a, redis, sessions)
        await reconcile_auction(a, redis, sessions)

    assert own.armed and own.max_amount == SECRET_MAX
    assert [(x.amount) for x in others[0].automatic_bids] == [RUNNER_UP_MAX, RUNNER_UP_MAX + 100]
    assert others[1].accepted and [x.amount for x in others[1].automatic_bids] == [600_100]
    assert others[3].armed and [x.amount for x in others[3].automatic_bids] == [700_000, 700_100]
    assert others[4].accepted and [x.amount for x in others[4].automatic_bids] == [760_100]
    assert others[5].accepted and others[5].automatic_bids == ()  # alice exhausted at 800_000

    secret = str(SECRET_MAX)
    for result in others:
        assert secret not in repr(result), result
    assert secret not in caplog.text, "the leading maximum reached a log line"
    for record in caplog.records:
        fields = getattr(record, "fields", {})
        if record.getMessage() == "proxy":
            assert "amount" not in fields and "max" not in str(fields).lower()

    audit = await db.fetch(
        "SELECT before, after, reason FROM audit_log WHERE entity_id LIKE $1 OR entity_id = $2",
        f"{a}%",
        str(a),
    )
    assert secret not in "".join(str(tuple(r)) for r in audit)
    # the ledger shows only resulting bid amounts (FR-51): alice's leading maximum is absent
    amounts = {
        r["amount"] for r in await db.fetch("SELECT amount FROM bids WHERE auction_id = $1", a)
    }
    assert SECRET_MAX not in amounts and RUNNER_UP_MAX in amounts
