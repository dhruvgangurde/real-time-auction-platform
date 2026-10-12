"""FR-14, FR-55, D1: original_end_time follows end_time while the auction is draft or scheduled
and is frozen from go-live, so end_time - original_end_time is the total soft-close extension."""

from datetime import timedelta

import asyncpg
import pytest
from asyncpg.exceptions import CheckViolationError

from tests.db.conftest import NOW, expect_error, make_auction


@pytest.mark.parametrize("status", ["live", "closing", "closed_sold", "cancelled"])
async def test_changing_original_end_time_fails_once_live(
    owner: asyncpg.Connection, status: str
) -> None:
    auction = await make_auction(owner, status=status)
    info = await expect_error(
        owner,
        CheckViolationError,
        "UPDATE auctions SET original_end_time = $2 WHERE id = $1",
        auction,
        NOW + timedelta(hours=2),
    )
    assert "original_end_time" in str(info.value)


async def test_extending_end_time_keeps_original(owner: asyncpg.Connection) -> None:
    """Soft close moves end_time and extension_count; original_end_time stays put."""
    auction = await make_auction(owner)
    await owner.execute(
        "UPDATE auctions SET end_time = end_time + interval '120 seconds', "
        "extension_count = extension_count + 1, status = 'live' WHERE id = $1",
        auction,
    )
    row = await owner.fetchrow(
        "SELECT end_time, original_end_time, extension_count FROM auctions WHERE id = $1",
        auction,
    )
    assert row["original_end_time"] == NOW + timedelta(hours=1)
    assert row["end_time"] == NOW + timedelta(hours=1, seconds=120)
    assert row["extension_count"] == 1


async def test_setting_original_end_time_to_same_value_is_allowed(
    owner: asyncpg.Connection,
) -> None:
    """A no-op write of the same value is not a modification."""
    auction = await make_auction(owner)
    await owner.execute(
        "UPDATE auctions SET original_end_time = original_end_time WHERE id = $1", auction
    )


@pytest.mark.parametrize("status", ["draft", "scheduled"])
async def test_original_end_time_follows_edits_before_go_live(
    owner: asyncpg.Connection, status: str
) -> None:
    """D1: a seller's pre-live edit of the end time keeps original_end_time equal to it."""
    auction = await make_auction(owner, status=status)
    new_end = NOW + timedelta(hours=3)
    await owner.execute(
        "UPDATE auctions SET end_time = $2, original_end_time = $2 WHERE id = $1", auction, new_end
    )
    assert (
        await owner.fetchval("SELECT original_end_time FROM auctions WHERE id = $1", auction)
        == new_end
    )
