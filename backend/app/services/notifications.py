"""Notifications (FR-70 to FR-74, SI-4).

Every notification is a row first (recipient, type, payload, created, read), written by its
producer with a dedupe_key so a retried producer writes nothing twice (DC-5):

- close (closing.py): auction_won, payment_due, auction_sold / auction_no_sale, auction_lost
- payments (payments.py): payment_received, payment_failed
- outbid (here, from a task queued after the bid acknowledgement): outbid:{auction}:{user}:{seq}
- ending soon (here, beat): ending_soon:{auction}:{user}, once per auction and watcher

In-app delivery is the row plus a live push on the recipient's channel (FR-71); the outbid
row is not pushed, because the bid path already sends the live outbid_notice. Email is a
separate, asynchronous step (FR-72): a beat task claims due groups with a lease, sends one
email per (recipient, type, auction) group, and retries with backoff. Nothing a producer does
waits for email, and an email failure never fails the producer.
"""

import logging
from collections import defaultdict
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.errors import AppError
from app.models import Notification
from app.models.notification import NOTIFICATION_TYPES
from app.repositories import (
    AuctionRepository,
    NotificationRepository,
    UserRepository,
    WatchlistRepository,
)
from app.services import live_events
from app.services.auth import CurrentUser
from app.services.email import EmailSender
from app.services.live_state import to_ms

log = logging.getLogger("notifications")

ENDING_SOON_BATCH = 1000


def rupees(paise: int | None) -> str:
    """Integer arithmetic only (DC-6)."""
    if paise is None:
        return "-"
    return f"₹{paise // 100:,}.{paise % 100:02d}"


def render(kind: str, payloads: list[dict[str, Any]]) -> tuple[str, str]:
    """Subject and body for one group of notifications of one type about one auction (FR-72).
    Payloads carry no reserve and no maximum (BR-6, BR-7), so neither can reach an email."""
    latest = payloads[-1]
    title = latest.get("title") or "an auction"
    count = len(payloads)
    price = latest.get("final_price", latest.get("amount"))
    lines = {
        "outbid": (
            f"You have been outbid on {title}",
            (f"You were outbid {count} times on {title}. " if count > 1 else "")
            + f"The current price is {rupees(latest.get('current_price'))}.",
        ),
        "ending_soon": (f"{title} ends soon", f"{title}, on your watchlist, ends soon."),
        "auction_won": (f"You won {title}", f"You won {title} for {rupees(price)}."),
        "auction_lost": (f"{title} has closed", f"{title} has closed; your bid did not win."),
        "payment_due": (f"Payment due for {title}", f"Please pay {rupees(price)} for {title}."),
        "payment_received": (
            f"Payment received for {title}",
            f"The payment of {rupees(price)} for {title} has been received.",
        ),
        "payment_failed": (
            f"Payment failed for {title}",
            f"A payment attempt for {title} failed. You can try again from the auction page.",
        ),
        "auction_sold": (f"{title} sold", f"{title} sold for {rupees(price)}."),
        "auction_no_sale": (f"{title} closed without a sale", f"{title} closed without a sale."),
    }
    subject, body = lines.get(kind, (f"Update on {title}", f"There is an update on {title}."))
    link = f"{settings.app_base_url}/auctions/{latest.get('auction_id', '')}"
    return subject, f"{body}\n\n{link}\n"


def view(n: Notification) -> dict[str, Any]:
    return {
        "id": n.id,
        "type": n.type,
        "auction_id": str(n.auction_id) if n.auction_id else None,
        "payload": n.payload,
        "read": n.read_at is not None,
        "created_at_ms": to_ms(n.created_at),
    }


class NotificationService:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], redis: Redis, email: EmailSender
    ) -> None:
        self._sessions, self._redis, self._email = sessions, redis, email

    # -------------------------------------------------------------- the user's view (FR-74)

    async def page(
        self, user: CurrentUser, *, unread: bool, before: int | None, limit: int
    ) -> dict[str, Any]:
        async with self._sessions() as session:
            rows = await NotificationRepository(session).page(
                user.id, unread=unread, before=before, limit=limit + 1
            )
        items = rows[:limit]
        return {
            "items": [view(n) for n in items],
            "next_before": items[-1].id if len(rows) > limit else None,
        }

    async def mark_read(self, user: CurrentUser, notification_id: int) -> None:
        async with self._sessions() as session, session.begin():
            if not await NotificationRepository(session).mark_read(user.id, notification_id):
                raise AppError("NOT_FOUND", "notification not found", 404)

    async def mark_all_read(self, user: CurrentUser) -> dict[str, int]:
        async with self._sessions() as session, session.begin():
            return {"updated": await NotificationRepository(session).mark_all_read(user.id)}

    async def preferences(self, user: CurrentUser) -> dict[str, dict[str, bool]]:
        async with self._sessions() as session:
            stored = await NotificationRepository(session).email_preferences([user.id])
        return {"email": {t: stored.get((user.id, t), True) for t in NOTIFICATION_TYPES}}

    async def set_preferences(
        self, user: CurrentUser, email: dict[str, bool]
    ) -> dict[str, dict[str, bool]]:
        async with self._sessions() as session, session.begin():
            await NotificationRepository(session).set_email_preferences(user.id, email)
        return await self.preferences(user)

    # -------------------------------------------------------------- producers (FR-70, FR-73)

    async def record_outbid(
        self, auction_id: UUID, users: list[UUID], sequence_no: int, price: int
    ) -> int:
        """The displaced bidders of one accepted bid; a retried bid or task writes nothing new."""
        async with self._sessions() as session, session.begin():
            auction = await AuctionRepository(session).get(auction_id)
            if auction is None or auction.is_demo:  # nothing demo reaches anyone (B2 Q2)
                return 0
            payload = {
                "auction_id": str(auction_id),
                "title": auction.title,
                "current_price": price,
                "sequence_no": sequence_no,
            }
            created = await NotificationRepository(session).add_many(
                [
                    {
                        "user_id": user,
                        "auction_id": auction_id,
                        "type": "outbid",
                        "payload": payload,
                        "dedupe_key": f"outbid:{auction_id}:{user}:{sequence_no}",
                    }
                    for user in users
                ]
            )
        return len(created)

    async def notify_ending_soon(self) -> int:
        """Watchers of live auctions within the lead interval, once per auction (FR-73)."""
        async with self._sessions() as session, session.begin():
            due = await WatchlistRepository(session).ending_soon(
                settings.ending_soon_lead_s, ENDING_SOON_BATCH
            )
            created = await NotificationRepository(session).add_many(
                [
                    {
                        "user_id": user,
                        "auction_id": auction_id,
                        "type": "ending_soon",
                        "payload": {
                            "auction_id": str(auction_id),
                            "title": title,
                            "end_time_ms": to_ms(end_time),
                        },
                        "dedupe_key": f"ending_soon:{auction_id}:{user}",
                    }
                    for user, auction_id, title, end_time in due
                ]
            )
        await live_events.publish_notifications(self._redis, created)
        return len(created)

    # -------------------------------------------------------------- email (FR-72, SI-4)

    async def dispatch_emails(self) -> dict[str, int]:
        async with self._sessions() as session, session.begin():
            claimed = await NotificationRepository(session).claim_email(
                settings.email_group_window_s, settings.email_lease_s, settings.email_batch
            )
        if not claimed:
            return {}
        groups: dict[tuple[UUID, str, UUID | None], list[Notification]] = defaultdict(list)
        for n in claimed:
            groups[(n.user_id, n.type, n.auction_id)].append(n)
        user_ids = list({n.user_id for n in claimed})
        async with self._sessions() as session:
            users = await UserRepository(session).get_many(user_ids)
            choices = await NotificationRepository(session).email_preferences(user_ids)
        counts: dict[str, int] = defaultdict(int)
        for (user_id, kind, _), rows in groups.items():
            rows.sort(key=lambda n: n.id)
            user = users.get(user_id)
            if (
                user is None
                or user.is_demo  # backstop: no demo account is ever emailed (B2 Q2)
                or user.status != "active"
                or not choices.get((user_id, kind), True)
            ):
                status = "skipped"  # in-app delivery is unaffected (FR-71)
                async with self._sessions() as session, session.begin():
                    await NotificationRepository(session).finish_email([n.id for n in rows], status)
            else:
                status = await self._send(user.email, kind, rows)
            counts[status] += 1
        return dict(counts)

    async def _send(self, address: str, kind: str, rows: list[Notification]) -> str:
        ids = [n.id for n in rows]
        subject, body = render(kind, [n.payload for n in rows])
        try:
            await self._email.send(address, subject, body)
        except Exception as exc:
            attempts = max(n.email_attempts for n in rows)
            async with self._sessions() as session, session.begin():
                repo = NotificationRepository(session)
                if attempts >= settings.email_max_attempts:
                    await repo.finish_email(ids, "failed")
                else:
                    await repo.retry_email(ids, 60 * 2 ** (attempts - 1))
            fields = {"notifications": ids, "type": kind, "attempts": attempts, "error": repr(exc)}
            if attempts >= settings.email_max_attempts:
                log.critical(
                    "email not delivered after the last attempt",
                    extra={"fields": fields | {"alert": "EMAIL_DELIVERY_FAILED"}},
                )
                return "failed"
            log.warning("email delivery retried later", extra={"fields": fields})
            return "retry"
        async with self._sessions() as session, session.begin():
            await NotificationRepository(session).finish_email(ids, "sent")
        return "sent"
