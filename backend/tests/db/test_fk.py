"""OR-2: referential integrity is enforced by foreign keys."""

import uuid

import asyncpg
from asyncpg.exceptions import ForeignKeyViolationError

from tests.db.conftest import expect_error, make_auction, make_user


async def test_bid_for_missing_auction_rejected(owner: asyncpg.Connection) -> None:
    bidder = await make_user(owner)
    await expect_error(
        owner,
        ForeignKeyViolationError,
        "INSERT INTO bids (auction_id, bidder_id, amount, sequence_no, bid_type) "
        "VALUES ($1, $2, 11000, 1, 'manual')",
        uuid.uuid4(),
        bidder,
    )


async def test_auction_with_bids_cannot_be_deleted(owner: asyncpg.Connection) -> None:
    """ON DELETE RESTRICT: nothing referenced is ever deleted out from under the ledger."""
    auction = await make_auction(owner)
    bidder = await make_user(owner)
    await owner.execute(
        "INSERT INTO bids (auction_id, bidder_id, amount, sequence_no, bid_type) "
        "VALUES ($1, $2, 11000, 1, 'manual')",
        auction,
        bidder,
    )
    await expect_error(
        owner, ForeignKeyViolationError, "DELETE FROM auctions WHERE id = $1", auction
    )
