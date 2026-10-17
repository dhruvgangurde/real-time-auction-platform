"""Demo mode schema (migration 0011): demo flags, the demo settlement status and demo_runs.

Demo lots settle as 'demo', never payable (B2 Q1); at most one active run per kind, enforced by
a partial unique index so it holds across both API instances (brief 12.4).
"""

import asyncpg
import pytest
from asyncpg.exceptions import (
    CheckViolationError,
    InsufficientPrivilegeError,
    UniqueViolationError,
)

from tests.db.conftest import expect_error, make_auction, make_user


async def test_demo_flags_default_false(owner: asyncpg.Connection) -> None:
    user = await make_user(owner)
    auction = await make_auction(owner)
    assert await owner.fetchval("SELECT is_demo FROM users WHERE id = $1", user) is False
    assert await owner.fetchval("SELECT is_demo FROM auctions WHERE id = $1", auction) is False


async def test_demo_settlement_status_needs_a_winner(owner: asyncpg.Connection) -> None:
    auction = await make_auction(owner, is_demo=True)
    winner = await make_user(owner)
    await owner.execute(
        "INSERT INTO settlements (auction_id, winner_id, final_price, status, idempotency_key) "
        "VALUES ($1, $2, 11000, 'demo', 'demo-1')",
        auction,
        winner,
    )
    other = await make_auction(owner, is_demo=True)
    info = await expect_error(
        owner,
        CheckViolationError,
        "INSERT INTO settlements (auction_id, status, idempotency_key) "
        "VALUES ($1, 'demo', 'demo-2')",
        other,
    )
    assert info.value.constraint_name == "ck_settlements_winner_matches_status"


async def _run(conn: asyncpg.Connection, kind: str = "stress", state: str = "requested") -> str:
    return await conn.fetchval(
        "INSERT INTO demo_runs (kind, state) VALUES ($1, $2) RETURNING id", kind, state
    )


async def test_one_active_run_per_kind(owner: asyncpg.Connection) -> None:
    await _run(owner, state="verified")
    await _run(owner, state="failed")
    await _run(owner)  # terminal runs never block a new one
    await _run(owner, kind="hero", state="running")  # nor does the other kind
    info = await expect_error(
        owner,
        UniqueViolationError,
        "INSERT INTO demo_runs (kind, state) VALUES ('stress', 'running')",
    )
    assert info.value.constraint_name == "uq_demo_runs_active_kind"


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [("kind", "hero2", "ck_demo_runs_kind"), ("state", "paused", "ck_demo_runs_state")],
)
async def test_demo_run_checks(
    owner: asyncpg.Connection, column: str, value: str, constraint: str
) -> None:
    row = {"kind": "stress", "state": "aborted"} | {column: value}
    info = await expect_error(
        owner,
        CheckViolationError,
        "INSERT INTO demo_runs (kind, state) VALUES ($1, $2)",
        row["kind"],
        row["state"],
    )
    assert info.value.constraint_name == constraint


async def test_app_role_moves_runs_but_never_deletes(app: asyncpg.Connection) -> None:
    run = await _run(app)
    await app.execute("UPDATE demo_runs SET state = 'running', version = 1 WHERE id = $1", run)
    await expect_error(app, InsufficientPrivilegeError, "DELETE FROM demo_runs WHERE id = $1", run)
