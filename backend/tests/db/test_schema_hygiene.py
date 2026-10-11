"""DC-6, OR-1: no floating-point or numeric money columns, every timestamp is timestamptz."""

import asyncpg


async def test_no_float_or_numeric_columns(owner: asyncpg.Connection) -> None:
    rows = await owner.fetch(
        "SELECT table_name, column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'public' "
        "AND data_type IN ('real', 'double precision', 'numeric')"
    )
    assert [tuple(r) for r in rows] == []


async def test_no_naive_timestamps(owner: asyncpg.Connection) -> None:
    rows = await owner.fetch(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND data_type = 'timestamp without time zone'"
    )
    assert [tuple(r) for r in rows] == []


async def test_money_columns_are_bigint(owner: asyncpg.Connection) -> None:
    """Every money column is BIGINT paise."""
    money = {
        ("auctions", "starting_price"),
        ("auctions", "reserve_price"),
        ("auctions", "min_increment"),
        ("bids", "amount"),
        ("proxy_bids", "max_amount"),
        ("settlements", "final_price"),
        ("payments", "amount"),
    }
    rows = await owner.fetch(
        "SELECT table_name, column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'public'"
    )
    types = {(r["table_name"], r["column_name"]): r["data_type"] for r in rows}
    assert {col: types[col] for col in money} == {col: "bigint" for col in money}
