"""Producers and senders (FR-70, FR-71, FR-73, SI-4): stored outbid notifications after the
acknowledgement, ending-soon once per auction, live push, queued auth email, SMTP sender."""

import asyncio
import logging
from email.message import EmailMessage
from typing import Any
from uuid import UUID

import asyncpg
import pytest

from app.api import deps
from app.core.config import settings
from app.services import email as email_module
from app.services import live_events
from app.services.bidding import BidService
from app.services.email import QueuedEmailSender, SmtpEmailSender
from app.services.notifications import NotificationService
from app.workers import dispatch, tasks
from tests.bidding.conftest import PersistRecorder
from tests.closing.conftest import Room
from tests.db.conftest import make_user


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, list[UUID], int, int]] = []

    def __call__(self, auction_id: UUID, users: list[UUID], seq: int, price: int) -> None:
        self.calls.append((auction_id, users, seq, price))


async def _outbid_rows(db: asyncpg.Connection, auction_id: UUID) -> list[asyncpg.Record]:
    return await db.fetch(
        "SELECT user_id, payload->>'current_price' AS price, dedupe_key FROM notifications "
        "WHERE auction_id = $1 AND type = 'outbid' ORDER BY id",
        auction_id,
    )


async def test_outbid_rows_follow_the_acknowledged_bid(
    ended, redis, sessions, notification_service: NotificationService, db: asyncpg.Connection
) -> None:
    """FR-70, UC-01 5a: the displaced leader and an exhausted maximum get a stored outbid
    notification, recorded after the bid's acknowledgement; a retried bid or task adds none."""
    recorder = Recorder()
    service = BidService(redis, sessions, PersistRecorder(), recorder)
    auction_id = await ended(status="live", end_s=3600)
    leader, holder, newcomer = [await make_user(db) for _ in range(3)]
    first = await service.place_bid(auction_id, leader, 10_000, "a")
    await live_events.publish_bid_events(redis, auction_id, leader, first, service.record_outbid)
    proxy = await service.set_proxy(auction_id, holder, 11_000, "b")  # outbids the leader
    await live_events.publish_bid_events(redis, auction_id, holder, proxy, service.record_outbid)
    last = await service.place_bid(auction_id, newcomer, 12_000, "c")  # exhausts the maximum
    await live_events.publish_bid_events(redis, auction_id, newcomer, last, service.record_outbid)

    assert [set(users) for _, users, _, _ in recorder.calls] == [{leader}, {holder}]
    for call in recorder.calls + recorder.calls:  # each task delivered twice
        await notification_service.record_outbid(*call)
    rows = await _outbid_rows(db, auction_id)
    assert [(r["user_id"], int(r["price"])) for r in rows] == [
        (leader, proxy.automatic_bids[-1].amount),
        (holder, 12_000),
    ]


async def test_a_failing_recorder_never_touches_the_bid(
    ended, redis, sessions, db: asyncpg.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    """FR-40: the bid is acknowledged already; a broken queue only costs the stored row, and
    the room still hears about the bid."""

    def broken(*_: Any) -> None:
        raise ConnectionError("broker down")

    service = BidService(redis, sessions, PersistRecorder(), broken)
    auction_id = await ended(status="live", end_s=3600)
    first, second = await make_user(db), await make_user(db)
    await service.place_bid(auction_id, first, 10_000, "a")
    result = await service.place_bid(auction_id, second, 10_500, "b")
    assert result.accepted
    async with Room(auction_id) as room:
        with caplog.at_level(logging.ERROR, logger="live_events"):
            await live_events.publish_bid_events(
                redis, auction_id, second, result, service.record_outbid
            )
    assert [e["type"] for e in room.events] == ["bid_placed"]
    assert "outbid notification not queued" in caplog.messages


async def test_ending_soon_once_per_auction_and_watcher(
    ended, notification_service: NotificationService, db: asyncpg.Connection
) -> None:
    """FR-73: watchers of a live auction inside the lead interval, once, even after the end
    moves; pushed live (FR-71). An auction further out is not yet due."""
    watcher, other = await make_user(db), await make_user(db)
    soon = await ended(status="live", end_s=120, live_state=False)
    later = await ended(status="live", end_s=settings.ending_soon_lead_s + 600, live_state=False)
    for auction_id in (soon, later):
        await db.execute(
            "INSERT INTO watchlist (user_id, auction_id) VALUES ($1, $2), ($3, $2)",
            watcher,
            auction_id,
            other,
        )
    async with Room(channel=live_events.user_channel(watcher)) as channel:
        await notification_service.notify_ending_soon()
    await db.execute(
        "UPDATE auctions SET end_time = end_time + interval '60 seconds' WHERE id = $1", soon
    )
    await notification_service.notify_ending_soon()
    rows = await db.fetch(
        "SELECT user_id, auction_id FROM notifications WHERE type = 'ending_soon' "
        "AND auction_id = ANY($1::uuid[])",
        [soon, later],
    )
    assert sorted((r["user_id"], r["auction_id"]) for r in rows) == sorted(
        [(watcher, soon), (other, soon)]
    )
    pushed = [e for e in channel.events if e["type"] == "notification"]
    assert [(e["notification_type"], e["auction_id"]) for e in pushed] == [
        ("ending_soon", str(soon))
    ]


def test_auth_email_is_queued_without_logging_its_token(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """SI-4: the request hands the message to the worker; if the hand-off fails the log names
    the task, never the body with its token."""
    queued: list[tuple[str, list[Any], bool]] = []

    def record(name: str, args: list[Any], *, log_args: bool = True) -> None:
        queued.append((name, args, log_args))

    asyncio.run(QueuedEmailSender(record).send("a@example.com", "Verify", "token=SECRET"))
    assert queued == [("send_email", ["a@example.com", "Verify", "token=SECRET"], False)]

    future: asyncio.Future[object] = asyncio.new_event_loop().create_future()
    future.set_exception(ConnectionError("broker down"))
    with caplog.at_level(logging.ERROR, logger="workers"):
        dispatch._log_failure(future, "send_email", None)
        dispatch._log_failure(future, "persist_bids", ["x"])
    fields = [r.fields for r in caplog.records]
    assert "args" not in fields[0] and fields[1]["args"] == ["x"]
    assert "SECRET" not in caplog.text


def test_api_queues_auth_email_outside_console(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "email_backend", "smtp")
    assert isinstance(deps._email_sender(), QueuedEmailSender)
    monkeypatch.setattr(settings, "email_backend", "console")
    assert isinstance(email_module.email_sender_from_settings(), email_module.ConsoleEmailSender)


def test_send_email_task_retries_then_alerts(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    sent: list[tuple[str, str, str]] = []

    class Sender:
        async def send(self, to: str, subject: str, body: str) -> None:
            if "fail" in subject:
                raise ConnectionError("smtp down")
            sent.append((to, subject, body))

    monkeypatch.setattr(tasks, "email_sender_from_settings", Sender)
    monkeypatch.setattr(tasks.runtime, "run", asyncio.run)
    tasks.send_email.run("a@example.com", "Hello", "token=SECRET")
    assert sent == [("a@example.com", "Hello", "token=SECRET")]
    task = tasks.send_email
    task.push_request(id="e1", retries=5, called_directly=True)
    try:
        with caplog.at_level(logging.CRITICAL, logger="workers"), pytest.raises(ConnectionError):
            task.run("a@example.com", "please fail", "token=SECRET")
    finally:
        task.pop_request()
    assert [r.fields["alert"] for r in caplog.records] == ["EMAIL_DELIVERY_FAILED"]
    assert "SECRET" not in caplog.text


def test_smtp_sender(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any SMTP server through configuration (TBD-1); a title cannot inject a header."""
    seen: list[Any] = []

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            seen.append(("connect", host, port))

        def __enter__(self) -> "FakeSMTP":
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def starttls(self) -> None:
            seen.append(("starttls",))

        def login(self, user: str, password: str) -> None:
            seen.append(("login", user))

        def send_message(self, message: EmailMessage) -> None:
            seen.append(("send", message["To"], message["Subject"], message.get_content()))

    monkeypatch.setattr(email_module.smtplib, "SMTP", FakeSMTP)
    sender = SmtpEmailSender(
        "smtp.example.com",
        587,
        username="u",
        password="p",
        starttls=True,
        sender="Auction <no-reply@example.com>",
        timeout_s=5,
    )
    asyncio.run(sender.send("b@example.com", "Sold\r\nBcc: evil@example.com", "body"))
    assert seen == [
        ("connect", "smtp.example.com", 587),
        ("starttls",),
        ("login", "u"),
        ("send", "b@example.com", "Sold Bcc: evil@example.com", "body\n"),
    ]
