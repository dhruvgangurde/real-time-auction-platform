"""OR-3, DC-5: uniqueness enforced by the database, not by application code."""

import asyncpg
from asyncpg.exceptions import UniqueViolationError

from tests.db.conftest import (
    expect_error,
    make_auction,
    make_bid,
    make_settlement,
    make_user,
)


async def test_bid_sequence_unique_per_auction(owner: asyncpg.Connection) -> None:
    """FR-41, OR-3: one ledger row per (auction, sequence_no)."""
    auction = await make_auction(owner)
    bidder = await make_user(owner)
    await make_bid(owner, auction, bidder, 1)
    await expect_error(
        owner,
        UniqueViolationError,
        "INSERT INTO bids (auction_id, bidder_id, amount, sequence_no, bid_type) "
        "VALUES ($1, $2, 12000, 1, 'manual')",
        auction,
        bidder,
    )
    # same sequence number on a different auction is fine
    other = await make_auction(owner)
    await make_bid(owner, other, bidder, 1)


async def test_one_settlement_per_auction(owner: asyncpg.Connection) -> None:
    """FR-61, OR-3: a second settlement for the same auction is rejected."""
    auction = await make_auction(owner)
    winner = await make_user(owner)
    await make_settlement(owner, auction, winner)
    await expect_error(
        owner,
        UniqueViolationError,
        "INSERT INTO settlements (auction_id, status, idempotency_key) "
        "VALUES ($1, 'no_sale', 'another-key')",
        auction,
    )


async def test_settlement_idempotency_key_unique(owner: asyncpg.Connection) -> None:
    """DC-5, OR-3: settlement creation is idempotent by key."""
    winner = await make_user(owner)
    await make_settlement(owner, await make_auction(owner), winner, key="close:1")
    other = await make_auction(owner)
    await expect_error(
        owner,
        UniqueViolationError,
        "INSERT INTO settlements (auction_id, status, idempotency_key) "
        "VALUES ($1, 'no_sale', 'close:1')",
        other,
    )


async def test_payment_gateway_refs_and_idempotency_key_unique(owner: asyncpg.Connection) -> None:
    """FR-66, FR-67, DC-5, OR-3: a redelivered callback cannot create a second record; one row
    per gateway order and per captured gateway payment."""
    winner = await make_user(owner)
    settlement = await make_settlement(owner, await make_auction(owner), winner)
    insert = (
        "INSERT INTO payments (settlement_id, amount, status, gateway_payment_id, "
        "gateway_order_id, idempotency_key) VALUES ($1, 11000, 'failed', $2, $3, $4)"
    )
    await owner.execute(insert, settlement, "pay_ref_1", "order_1", "idem_1")
    await expect_error(
        owner, UniqueViolationError, insert, settlement, "pay_ref_1", "order_2", "idem_2"
    )
    await expect_error(
        owner, UniqueViolationError, insert, settlement, "pay_ref_2", "order_2", "idem_1"
    )
    await expect_error(
        owner, UniqueViolationError, insert, settlement, "pay_ref_3", "order_1", "idem_3"
    )


async def test_payment_retry_allowed_but_one_succeeded(owner: asyncpg.Connection) -> None:
    """FR-67, DC-5: failed then succeeded is allowed; two succeeded is not."""
    winner = await make_user(owner)
    settlement = await make_settlement(owner, await make_auction(owner), winner)
    insert = (
        "INSERT INTO payments (settlement_id, amount, status, gateway_payment_id, "
        "gateway_order_id, idempotency_key) VALUES ($1, 11000, $2, $3, $4, $5)"
    )
    await owner.execute(insert, settlement, "failed", "ref_a", "order_a", "key_a")
    await owner.execute(insert, settlement, "succeeded", "ref_b", "order_b", "key_b")
    await expect_error(
        owner, UniqueViolationError, insert, settlement, "succeeded", "ref_c", "order_c", "key_c"
    )


async def test_payment_event_dedupe_key_unique(owner: asyncpg.Connection) -> None:
    """FR-67, DC-5: a gateway event is applied at most once."""
    insert = (
        "INSERT INTO payment_events (dedupe_key, source, event, outcome) "
        "VALUES ($1, 'webhook', 'payment.captured', 'succeeded')"
    )
    await owner.execute(insert, "webhook:evt_1")
    await expect_error(owner, UniqueViolationError, insert, "webhook:evt_1")


async def test_one_active_proxy_per_auction_and_bidder(owner: asyncpg.Connection) -> None:
    """FR-45, OR-3: at most one active maximum; re-arming after deactivation is allowed."""
    auction = await make_auction(owner)
    bidder = await make_user(owner)
    insert = "INSERT INTO proxy_bids (auction_id, bidder_id, max_amount) VALUES ($1, $2, $3)"
    await owner.execute(insert, auction, bidder, 20_000)
    await expect_error(owner, UniqueViolationError, insert, auction, bidder, 30_000)
    await owner.execute(
        "UPDATE proxy_bids SET is_active = false WHERE auction_id = $1 AND bidder_id = $2",
        auction,
        bidder,
    )
    await owner.execute(insert, auction, bidder, 30_000)


async def test_email_unique_case_insensitive(owner: asyncpg.Connection) -> None:
    """FR-1: lower(email) is unique."""
    await make_user(owner, "Alice@Example.com")
    await expect_error(
        owner,
        UniqueViolationError,
        "INSERT INTO users (email, password_hash, full_name) VALUES ($1, 'x', 'A')",
        "alice@example.com",
    )


async def test_notification_dedupe_key_unique(owner: asyncpg.Connection) -> None:
    """DC-5: notification dispatch is idempotent by dedupe_key."""
    user = await make_user(owner)
    insert = (
        "INSERT INTO notifications (user_id, type, payload, dedupe_key) "
        "VALUES ($1, 'outbid', '{}'::jsonb, $2)"
    )
    await owner.execute(insert, user, "outbid:a:1")
    await expect_error(owner, UniqueViolationError, insert, user, "outbid:a:1")
