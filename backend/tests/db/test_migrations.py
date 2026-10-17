"""OR-4: migrations are versioned and reversible.

Uses its own throwaway database so `downgrade base` never wipes tables other tests rely on.
"""

import asyncio
import subprocess

import asyncpg
import pytest

from tests.db.conftest import (
    drop_database,
    make_auction,
    make_user,
    owner_dsn,
    recreate_database,
    run_alembic,
)

MIG_DB = "auction_migtest"


def test_upgrade_downgrade_upgrade_cycle_and_no_drift() -> None:
    recreate_database(MIG_DB)
    try:
        run_alembic(MIG_DB, "upgrade", "head")
        run_alembic(MIG_DB, "downgrade", "base")
        run_alembic(MIG_DB, "upgrade", "head")
        # models and migrations describe the same schema
        out = run_alembic(MIG_DB, "check")
        assert "No new upgrade operations detected" in out.stdout + out.stderr
    finally:
        drop_database(MIG_DB)


def test_0011_downgrade_refuses_while_demo_settlements_exist() -> None:
    """A demo settlement is never turned into a payable one by a downgrade (B2 Q1)."""

    async def seed() -> None:
        conn = await asyncpg.connect(owner_dsn(MIG_DB))
        try:
            auction = await make_auction(conn, is_demo=True)
            await conn.execute(
                "INSERT INTO settlements (auction_id, winner_id, final_price, status, "
                "idempotency_key) VALUES ($1, $2, 11000, 'demo', 'k')",
                auction,
                await make_user(conn),
            )
        finally:
            await conn.close()

    recreate_database(MIG_DB)
    try:
        run_alembic(MIG_DB, "upgrade", "head")
        asyncio.run(seed())
        with pytest.raises(subprocess.CalledProcessError) as info:
            run_alembic(MIG_DB, "downgrade", "0010")
        assert "ck_settlements_status" in info.value.stderr
    finally:
        drop_database(MIG_DB)
