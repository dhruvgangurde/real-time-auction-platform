"""A proxy maximum is disclosed to nobody but its owner (FR-51, BR-7, NFR-27).

Under FR-49 the final price reveals the runner-up's level by construction (second-price
property), and the runner-up's record bid at its own maximum discloses nothing beyond that.
BR-7 protects the leading maximum: it is never disclosed *as a maximum*. An automatic bid can
still equal it, because the leader bids target = min(max, competing + increment): when a rival
comes within one increment, the resulting bid is the maximum itself, as in architecture.md's
worked example (Erin's 4995 makes Alice bid 5000, her maximum). That amount is a public bid like
any other; nothing marks it as a maximum (audit M1).

The first test arms a leading maximum with a distinctive value that is never reached and asserts
it appears nowhere except in its owner's own result: not in other bidders' results, not in any
log line, not in audit_log.
"""

import dataclasses
import logging

import asyncpg
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.bidding import AutomaticBid, BidResult, BidService
from app.services.ledger import persist_outbox
from app.services.live_events import bid_events
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


async def test_a_maximum_can_be_the_resulting_bid_but_nothing_labels_it(
    service: BidService,
    auction: AuctionFactory,
    new_user,
    redis: Redis,
    sessions: async_sessionmaker[AsyncSession],
    db: asyncpg.Connection,
) -> None:
    """M1, FR-49, BR-7: maximum ₹16,000, increment ₹500, a rival bids ₹15,500. The leader answers
    at min(16,000, 15,500 + 500) = ₹16,000: the maximum itself, as a public automatic bid. No
    field anyone else receives, and no ledger column, says that it was a maximum."""
    a = await auction(start_price=1_000_000, min_increment=50_000)
    alice, bob = await new_user(), await new_user()
    own = await service.set_proxy(a, alice, 1_600_000, "alice-arm")
    assert own.armed and own.max_amount == 1_600_000  # her own result: hers to see (FR-51)

    rival = await service.place_bid(a, bob, 1_550_000, "bob-bid")
    assert rival.accepted and rival.amount == 1_550_000
    assert [(x.bidder_id, x.amount) for x in rival.automatic_bids] == [(alice, 1_600_000)]

    # what bob receives carries no field about a maximum
    names = [f.name for f in dataclasses.fields(BidResult) + dataclasses.fields(AutomaticBid)]
    assert not [n for n in names if "max" in n], names
    # what the room receives: two bid_placed events, manual then automatic, no maximum field
    room, notices = bid_events(a, bob, rival)
    placed = [e for e in room if e["type"] == "bid_placed"]
    assert [(e["amount"], e["bid_type"]) for e in placed] == [
        (1_550_000, "manual"),
        (1_600_000, "automatic"),
    ]
    for frame in room + list(notices.values()):
        assert not [k for k in frame if "max" in k], frame
    # the ledger: an automatic bid at that amount, and no column that could mark a maximum
    await persist_outbox(a, redis, sessions)
    rows = await db.fetch("SELECT * FROM bids WHERE auction_id = $1 ORDER BY sequence_no", a)
    assert (rows[-1]["amount"], rows[-1]["bid_type"]) == (1_600_000, "automatic")
    assert not [c for c in rows[-1].keys() if "max" in c]
