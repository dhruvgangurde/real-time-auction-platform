"""DC-4, FR-79, BR-13: bids and audit_log are append-only for the application role.

Parent rows are created through the app connection itself, because rows inserted by the
owner connection are uncommitted and invisible to this one.
"""

import json

import asyncpg
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from asyncpg.exceptions import InsufficientPrivilegeError

from app.core.config import settings
from tests.db.conftest import BACKEND_DIR, expect_error, make_auction, make_bid, make_user


async def test_app_role_is_not_the_owner(app: asyncpg.Connection) -> None:
    assert await app.fetchval("SELECT current_user") == settings.app_db_user


async def test_bids_insert_and_select_allowed(app: asyncpg.Connection) -> None:
    auction = await make_auction(app)
    bidder = await make_user(app)
    bid_id = await make_bid(app, auction, bidder, 1)
    assert await app.fetchval("SELECT amount FROM bids WHERE id = $1", bid_id) == 11000


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE bids SET amount = 1 WHERE id = $1",
        "DELETE FROM bids WHERE id = $1",
    ],
)
async def test_bids_update_delete_denied(app: asyncpg.Connection, sql: str) -> None:
    auction = await make_auction(app)
    bid_id = await make_bid(app, auction, await make_user(app), 1)
    await expect_error(app, InsufficientPrivilegeError, sql, bid_id)


async def test_bids_truncate_denied(app: asyncpg.Connection) -> None:
    await expect_error(app, InsufficientPrivilegeError, "TRUNCATE bids")


async def _audit(app: asyncpg.Connection) -> int:
    return await app.fetchval(
        "INSERT INTO audit_log (actor_id, entity_type, entity_id, action, after) "
        "VALUES (NULL, 'auction', 'a1', 'created', $1::jsonb) RETURNING id",
        json.dumps({"status": "draft"}),
    )


async def test_audit_insert_and_select_allowed(app: asyncpg.Connection) -> None:
    audit_id = await _audit(app)
    assert await app.fetchval("SELECT action FROM audit_log WHERE id = $1", audit_id) == "created"


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE audit_log SET action = 'tampered' WHERE id = $1",
        "DELETE FROM audit_log WHERE id = $1",
    ],
)
async def test_audit_update_delete_denied(app: asyncpg.Connection, sql: str) -> None:
    audit_id = await _audit(app)
    await expect_error(app, InsufficientPrivilegeError, sql, audit_id)


async def test_audit_truncate_denied(app: asyncpg.Connection) -> None:
    await expect_error(app, InsufficientPrivilegeError, "TRUNCATE audit_log")


async def test_ordinary_table_update_allowed(app: asyncpg.Connection) -> None:
    auction = await make_auction(app)
    await app.execute("UPDATE auctions SET status = 'scheduled' WHERE id = $1", auction)
    assert await app.fetchval("SELECT status FROM auctions WHERE id = $1", auction) == "scheduled"


async def test_migration_state_read_only(app: asyncpg.Connection) -> None:
    """The app role can read alembic_version but never change it."""
    head = ScriptDirectory.from_config(Config(str(BACKEND_DIR / "alembic.ini"))).get_current_head()
    assert await app.fetchval("SELECT version_num FROM alembic_version") == head
    await expect_error(
        app, InsufficientPrivilegeError, "UPDATE alembic_version SET version_num = 'x'"
    )
    await expect_error(app, InsufficientPrivilegeError, "DELETE FROM alembic_version")


async def test_payment_events_append_only(app: asyncpg.Connection) -> None:
    """DC-4, FR-67: applied gateway events are evidence; the app role can only add them."""
    await app.execute(
        "INSERT INTO payment_events (dedupe_key, source, event, outcome) "
        "VALUES ('callback:pay_append_only', 'callback', 'payment.captured', 'succeeded')"
    )
    for sql in (
        "UPDATE payment_events SET outcome = 'failed'",
        "DELETE FROM payment_events",
        "TRUNCATE payment_events",
    ):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with app.transaction():
                await app.execute(sql)
