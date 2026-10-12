"""CHECK constraints on auctions and settlements (DC-6, FR-14, FR-54, FR-61)."""

from datetime import timedelta

import asyncpg
import pytest
from asyncpg.exceptions import CheckViolationError

from tests.db.conftest import NOW, make_auction, make_category, make_user


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"end_time": NOW, "original_end_time": NOW}, "ck_auctions_end_after_start"),
        ({"starting_price": -1}, "ck_auctions_starting_price_pos"),
        ({"starting_price": 0}, "ck_auctions_starting_price_pos"),
        ({"min_increment": 0}, "ck_auctions_min_increment_pos"),
        ({"reserve_price": 9_999}, "ck_auctions_reserve_ge_start"),
        ({"extension_count": 11}, "ck_auctions_extension_count_range"),
    ],
)
async def test_auction_checks(owner: asyncpg.Connection, overrides: dict, constraint: str) -> None:
    """DC-6 money sanity, FR-54 extension cap of 10."""
    seller = await make_user(owner)
    category = await make_category(owner)
    with pytest.raises(CheckViolationError) as info:
        async with owner.transaction():
            await make_auction(owner, seller_id=seller, category_id=category, **overrides)
    assert info.value.constraint_name == constraint


async def test_auction_end_before_start_rejected(owner: asyncpg.Connection) -> None:
    """End strictly after start."""
    with pytest.raises(CheckViolationError):
        async with owner.transaction():
            await make_auction(owner, end_time=NOW - timedelta(seconds=1))


async def test_no_sale_settlement_with_winner_rejected(owner: asyncpg.Connection) -> None:
    """FR-61: a no-sale leaves winner and price unset."""
    auction = await make_auction(owner)
    winner = await make_user(owner)
    with pytest.raises(CheckViolationError) as info:
        async with owner.transaction():
            await owner.execute(
                "INSERT INTO settlements (auction_id, winner_id, final_price, status, "
                "idempotency_key) VALUES ($1, $2, 11000, 'no_sale', 'k1')",
                auction,
                winner,
            )
    assert info.value.constraint_name == "ck_settlements_winner_matches_status"


async def test_sold_settlement_without_winner_rejected(owner: asyncpg.Connection) -> None:
    """FR-61: a sale records the winner and the final price."""
    auction = await make_auction(owner)
    with pytest.raises(CheckViolationError) as info:
        async with owner.transaction():
            await owner.execute(
                "INSERT INTO settlements (auction_id, status, idempotency_key) "
                "VALUES ($1, 'pending_payment', 'k2')",
                auction,
            )
    assert info.value.constraint_name == "ck_settlements_winner_matches_status"
