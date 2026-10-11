"""OR-4: migrations are versioned and reversible.

Uses its own throwaway database so `downgrade base` never wipes tables other tests rely on.
"""

from tests.db.conftest import drop_database, recreate_database, run_alembic

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
