"""Email delivery of stored notifications (FR-72, SI-4): due groups combined into one email,
preferences respected, retried with backoff, never sent twice by concurrent dispatchers, and
never holding a lock that blocks the user's own reads and writes."""

import asyncio
import logging

import asyncpg
import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.services.notifications import NotificationService, render
from tests.db.conftest import make_auction
from tests.lifecycle.conftest import bearer
from tests.notifications.support import Outbox, add_notification, email_rows, seconds_until


async def _user_email(db: asyncpg.Connection, user_id) -> str:
    return await db.fetchval("SELECT email FROM users WHERE id = $1", user_id)


async def test_repeats_within_the_window_leave_as_one_email(
    make_user, notification_service: NotificationService, outbox: Outbox, db: asyncpg.Connection
) -> None:
    """FR-72: five outbids for one user and auction become one email once the oldest is older
    than the window; a group whose oldest is still young waits."""
    user_id, _ = await make_user()
    auction, quiet = await make_auction(db), await make_auction(db)
    group = [await add_notification(db, user_id, auction, age_s=90)]
    group += [await add_notification(db, user_id, auction, age_s=5) for _ in range(4)]
    waiting = await add_notification(db, user_id, quiet, age_s=10)

    assert await notification_service.dispatch_emails() == {"sent": 1}
    ((to, subject, body),) = outbox.sent
    assert to == await _user_email(db, user_id)
    assert subject == "You have been outbid on Brass telescope"
    assert "outbid 5 times" in body and "₹123.00" in body
    assert [r["email_status"] for r in await email_rows(db, group)] == ["sent"] * 5
    assert [r["email_status"] for r in await email_rows(db, [waiting])] == ["pending"]
    assert await notification_service.dispatch_emails() == {}  # nothing new is due


async def test_preferences_and_inactive_accounts_skip_email_only(
    api: AsyncClient,
    make_user,
    notification_service: NotificationService,
    outbox: Outbox,
    db: asyncpg.Connection,
) -> None:
    """FR-72, FR-71: email follows the per-type preference and an active account; the in-app
    notification is there either way."""
    user_id, token = await make_user()
    suspended_id, _ = await make_user()
    await db.execute("UPDATE users SET status = 'suspended' WHERE id = $1", suspended_id)
    auction = await make_auction(db)
    response = await api.put(
        "/api/v1/notifications/preferences",
        json={"email": {"outbid": False}},
        headers=bearer(token),
    )
    assert response.json()["email"]["outbid"] is False
    muted = await add_notification(db, user_id, auction, age_s=90)
    won = await add_notification(
        db,
        user_id,
        auction,
        "auction_won",
        age_s=90,
        payload={"title": "Lamp", "final_price": 5_000},
    )
    blocked = await add_notification(db, suspended_id, auction, age_s=90)

    assert await notification_service.dispatch_emails() == {"skipped": 2, "sent": 1}
    assert [s for _, s, _ in outbox.sent] == ["You won Lamp"]
    rows = {r["id"]: r["email_status"] for r in await email_rows(db, [muted, won, blocked])}
    assert rows == {muted: "skipped", won: "sent", blocked: "skipped"}
    listed = await api.get("/api/v1/notifications", headers=bearer(token))
    assert {n["id"] for n in listed.json()["items"]} >= {muted, won}


async def test_failures_back_off_then_alert(
    make_user,
    notification_service: NotificationService,
    outbox: Outbox,
    db: asyncpg.Connection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """FR-72: retried after 1, 2, 4 and 8 minutes; after the fifth attempt the group is failed
    and EMAIL_DELIVERY_FAILED is raised. Nothing else is affected."""
    user_id, _ = await make_user()
    row = await add_notification(db, user_id, await make_auction(db), age_s=90)
    outbox.failures = 99
    delays = []
    for _ in range(4):
        assert await notification_service.dispatch_emails() == {"retry": 1}
        (state,) = await email_rows(db, [row])
        delays.append(round(seconds_until(state) / 60))
        await db.execute("UPDATE notifications SET email_next_at = now() WHERE id = $1", row)
    assert delays == [1, 2, 4, 8]
    with caplog.at_level(logging.CRITICAL, logger="notifications"):
        assert await notification_service.dispatch_emails() == {"failed": 1}
    (state,) = await email_rows(db, [row])
    assert (state["email_status"], state["email_attempts"]) == ("failed", 5)
    alerts = [r.fields["alert"] for r in caplog.records if "alert" in getattr(r, "fields", {})]
    assert alerts == ["EMAIL_DELIVERY_FAILED"]
    assert outbox.sent == []


async def test_concurrent_dispatchers_send_each_group_once(
    make_user,
    sessions,
    redis,
    outbox: Outbox,
    db: asyncpg.Connection,
) -> None:
    """FR-72, DC-5: the claim (SKIP LOCKED plus a lease) gives each group to one dispatcher.
    Only this test's users are counted: rows other tests leave pending become due after 60 s,
    and any dispatcher sends them too (the stranger's row stands in for one)."""
    auction = await make_auction(db)
    users = [(await make_user())[0] for _ in range(4)]
    for user_id in users:
        for _ in range(3):
            await add_notification(db, user_id, auction, age_s=90)
    stranger = (await make_user())[0]  # a row another test left pending, as the suite can
    await add_notification(db, stranger, auction, age_s=90)
    dispatchers = [NotificationService(sessions, redis, outbox) for _ in range(4)]
    await asyncio.gather(*(d.dispatch_emails() for d in dispatchers))
    emails = {
        r["email"] for r in await db.fetch("SELECT email FROM users WHERE id = ANY($1)", users)
    }
    mine = [to for to, _, _ in outbox.sent if to in emails]
    assert len(mine) == 4  # one email per user's group, from whichever dispatcher claimed it
    assert set(mine) == emails


async def test_a_slow_send_never_blocks_the_users_own_actions(
    api: AsyncClient,
    make_user,
    notification_service: NotificationService,
    outbox: Outbox,
    db: asyncpg.Connection,
) -> None:
    """The claim commits before the send, so marking read during a stuck SMTP call returns at
    once."""
    user_id, token = await make_user()
    row = await add_notification(db, user_id, await make_auction(db), age_s=90)
    outbox.gate = asyncio.Event()
    dispatch = asyncio.create_task(notification_service.dispatch_emails())
    while (await email_rows(db, [row]))[0]["email_attempts"] == 0:
        await asyncio.sleep(0.02)
    response = await asyncio.wait_for(
        api.post(f"/api/v1/notifications/{row}/read", headers=bearer(token)), timeout=2
    )
    assert response.status_code == 204
    read = await api.post("/api/v1/notifications/read-all", headers=bearer(token))
    assert read.json() == {"updated": 0}
    outbox.gate.set()
    assert await dispatch == {"sent": 1}


def test_render_never_carries_more_than_the_payload() -> None:
    """BR-6, BR-7: the email is built from the notification payload only (no reserve, no
    maximum is ever in one); amounts in rupees by integer arithmetic (DC-6)."""
    subject, body = render(
        "auction_sold",
        [{"auction_id": "a1", "title": "Clock\r\nBcc: x@y", "final_price": 1_050_075}],
    )
    assert "₹10,500.75" in body
    assert f"{settings.app_base_url}/auctions/a1" in body
    assert render("payment_failed", [{"title": "Clock"}])[0] == "Payment failed for Clock"
