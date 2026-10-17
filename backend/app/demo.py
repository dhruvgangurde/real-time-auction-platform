"""The demo seed for recording a walkthrough: `python -m app.cli seed-demo [--fresh]`, with
`--live-minutes N` (lot a, default 10) and `--starts-in M` (lot b, default 3).

Development only (APP_ENV=local), and never on the e2e stack (E2E_STACK=1). Every row comes
from the real services, so what a video shows is what the platform did:

- accounts are registered and verified by AuthService (the verification mail is caught in
  memory and its token consumed by verify_email); roles are granted by UserService, as
  `grant-role` does;
- listings are created, given a photo and published by AuctionService;
- bids are decided by BidService's Lua script, then the room and outbid events are published,
  as the REST endpoint does;
- going live and closing are left to the scheduler and the close task;
- the paid lot gets a Razorpay test-mode order, then the payment.captured webhook Razorpay
  would send for it. It is signed here with RAZORPAY_WEBHOOK_SECRET (held in memory, never
  printed) and applied by the real webhook handler; no checkout took place.

No row is written by SQL. Passwords: when DEMO_ACCOUNTS_PASSWORD is set (in .env, local demo
only) every account gets it, and it is never printed; otherwise each run generates them and
prints them once. Each run writes the accounts with their passwords, the six lots and the URLs
to demo-credentials.md in the host's DEMO_PRIVATE_DIR (../auction-private by default), outside
the repository, which docker-compose.override.yml mounts at /private.
"""

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import sys
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import SystemClock
from app.core.config import settings
from app.core.errors import AppError
from app.core.ratelimit import RateLimiter
from app.core.storage import LocalStorage
from app.repositories.auction import AuctionRepository
from app.repositories.category import CategoryRepository
from app.repositories.user import UserRepository
from app.schemas.auth import PASSWORD_MAX, PASSWORD_MIN
from app.services import live_events
from app.services.auctions import AuctionService
from app.services.auth import AuthService, CurrentUser
from app.services.bidding import BidService
from app.services.catalog import CatalogService
from app.services.closing import settlement_view
from app.services.denylist import Denylist
from app.services.notifications import rupees
from app.services.payment_gateway import razorpay_from_settings
from app.services.payments import PaymentService
from app.services.users import UserService
from app.workers.dispatch import enqueue, schedule_outbid, schedule_persist

IMAGES = Path(__file__).resolve().parents[1] / "demo" / "images"
WEB = "http://localhost:8011"  # the web service's default host port (CLAUDE.md section 4)
MAILPIT = "http://localhost:8025"  # Mailpit's web UI, dev only (CLAUDE.md section 4)
# the dev override mounts the host's DEMO_PRIVATE_DIR (../auction-private by default) here
CREDENTIALS = Path("/private/demo-credentials.md")
IST = timezone(timedelta(hours=5, minutes=30), "IST")
SOURCE = "seed-demo"  # stands in for the client address in rate limits
FROM_ENV = "password from DEMO_ACCOUNTS_PASSWORD"  # shown in its place: never the value


@dataclass(frozen=True)
class Person:
    name: str
    local: str  # the email's local part
    role: str | None = None  # granted on top of bidder


MEERA = Person("Meera Shah", "meera.shah", "seller")
ASHA = Person("Asha Rao", "asha.rao")
RAVI = Person("Ravi Kumar", "ravi.kumar")
ADMIN = Person("Kavya Nair", "kavya.nair", "admin")


@dataclass(frozen=True)
class Lot:
    title: str
    description: str
    category: str  # slug in the default tree
    image: str
    starting_price: int  # paise (DC-6)
    min_increment: int
    reserve_price: int | None = None


LOTS = {
    "a": Lot(
        'Ravi Varma Press chromolithograph, "Gauri"',
        "Chromolithograph from the Ravi Varma Fine Art Lithographic Press, Malavli, after the "
        "painting by Raja Ravi Varma. Early 20th century, about 50 x 35 cm, unframed. Strong "
        "colour with light toning at the margins and a small closed tear at the lower edge, "
        "clear of the image. Ships rolled in a tube within India.\n\n"
        "Photo of the same print: The Metropolitan Museum of Art, 2020.20, CC0.",
        "collectibles",
        "ravi-varma-gauri.jpg",
        starting_price=1_200_000,
        min_increment=50_000,
        reserve_price=1_500_000,
    ),
    "b": Lot(
        "Kashmir chain-stitch shawl, wool",
        "Hand-embroidered in chain stitch (amlikar) on fine wool, mid 20th century, about "
        "200 x 100 cm. Dense floral border; the field is slightly faded and has two small "
        "darns. Dry cleaned and stored flat.\n\n"
        "Photo of a comparable piece: Cleveland Museum of Art, 1940.600, CC0.",
        "fashion",
        "kashmir-shawl.jpg",
        starting_price=2_500_000,
        min_increment=100_000,
    ),
    "c": Lot(
        "Bidri betel box (pandan) with chevron inlay",
        "Zinc alloy blackened with Bidar soil and inlaid with silver in a chevron pattern; the "
        "hinged lid opens on three compartments for leaf, areca and lime. 19th century, 11 cm "
        "across, about 380 g. Inlay complete; light wear to the patina on the lid edge.\n\n"
        "Photo of a comparable piece: Cleveland Museum of Art, 2020.206, CC0.",
        "home-and-garden",
        "bidri-pandan.jpg",
        starting_price=1_800_000,
        min_increment=100_000,
        reserve_price=2_200_000,
    ),
    "d": Lot(
        "Apple Macintosh PowerBook 100 (1991) with charger",
        "Model M4400 with 4 MB of memory and a 20 MB hard disk, System 7.1 installed. Boots "
        "and runs; the trackball is smooth and the screen has no dead lines. Original charger "
        "included; the internal battery is spent and is not included. Light scuffs on the "
        "lid.\n\nPhoto: Danamania, Wikimedia Commons, CC0.",
        "laptops",
        "powerbook-100.jpg",
        starting_price=900_000,
        min_increment=50_000,
    ),
    "e": Lot(
        "Bidri hookah base with flowering plants",
        "Globular hookah base in bidri, the blackened body inlaid with silver and brass "
        "flowering plants above a band of leaves at the foot. 19th century, 19 cm high. A "
        "small dent near the base; the inlay is otherwise complete.\n\n"
        "Photo of a comparable piece: Cleveland Museum of Art, 2020.207, CC0.",
        "home-and-garden",
        "bidri-hookah.jpg",
        starting_price=3_000_000,
        min_increment=100_000,
        reserve_price=6_000_000,
    ),
    "f": Lot(
        "Kalighat painting: Krishna asks Yashoda for butter",
        "Watercolour and silver paint on paper, Kalighat, Kolkata, late 19th or early 20th "
        "century, about 46 x 28 cm. Mounted, not framed. Even toning; the colour is crisp.\n\n"
        "Photo of a comparable piece: Cleveland Museum of Art, 2003.112, CC0.",
        "collectibles",
        "kalighat-yashoda.jpg",
        starting_price=800_000,
        min_increment=50_000,
        reserve_price=1_000_000,
    ),
}

# c, d and e reach their state through real bids: (bidder, amount in paise), in order
BIDS = {
    "c": ((RAVI, 1_800_000), (ASHA, 2_000_000), (RAVI, 2_100_000), (ASHA, 2_400_000)),
    "d": ((ASHA, 900_000), (RAVI, 950_000), (ASHA, 1_050_000), (RAVI, 1_150_000)),
    "e": ((ASHA, 3_000_000), (RAVI, 3_200_000), (ASHA, 3_500_000)),  # below the reserve
}


def refusal() -> str | None:
    """Why the seed must not run here, or None: the plain dev stack only, and a configured
    password that registration would accept (FR-2), checked without ever showing it."""
    if settings.app_env != "local":
        return f"seed-demo runs only in development (APP_ENV=local), not APP_ENV={settings.app_env}"
    if settings.e2e_stack:
        return (
            "the e2e overlay is active (E2E_STACK=1); start the dev stack with "
            "`docker compose up -d` and run it again"
        )
    configured = settings.demo_accounts_password.get_secret_value()
    if configured and not PASSWORD_MIN <= len(configured) <= PASSWORD_MAX:
        return f"DEMO_ACCOUNTS_PASSWORD must be {PASSWORD_MIN} to {PASSWORD_MAX} characters (FR-2)"
    if not CREDENTIALS.parent.is_dir():
        return (
            f"{CREDENTIALS.parent} is not mounted, so the credentials file has nowhere to go: "
            "docker-compose.override.yml mounts DEMO_PRIVATE_DIR (../auction-private by default) "
            "there; recreate the api container with `docker compose up -d`"
        )
    return None


def account_password() -> str:
    """DEMO_ACCOUNTS_PASSWORD when set, otherwise a new one for each account."""
    return settings.demo_accounts_password.get_secret_value() or secrets.token_urlsafe(12)


def shown(password: str) -> str:
    """What the terminal shows for an account's password: never the configured one."""
    return FROM_ENV if settings.demo_accounts_password.get_secret_value() else password


class _Inbox:
    """Catches the verification mail, so the real verify_email consumes its token."""

    def __init__(self) -> None:
        self.tokens: dict[str, str] = {}

    async def send(self, to: str, subject: str, body: str) -> None:
        self.tokens[to] = body.split("#token=", 1)[1].split()[0]


async def _file(path: Path) -> AsyncIterator[bytes]:
    yield path.read_bytes()


def _say(line: str) -> None:
    print(line, flush=True)


class _Seeder:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], redis: Redis, users: UserService
    ) -> None:
        clock, storage = SystemClock(), LocalStorage(settings.media_root)
        limiter = RateLimiter(redis, clock)
        self.sessions, self.redis, self.users, self.clock = sessions, redis, users, clock
        self.inbox = _Inbox()
        self.auth = AuthService(
            sessions, redis, clock, self.inbox, limiter, Denylist(redis, enqueue)
        )
        self.auctions = AuctionService(sessions, redis, clock, limiter, storage)
        self.catalog = CatalogService(sessions, redis, storage)
        self.bids = BidService(redis, sessions, schedule_persist, schedule_outbid)
        self.payments = PaymentService(
            sessions,
            redis,
            razorpay_from_settings(),
            limiter,
            key_secret=settings.razorpay_key_secret,
            webhook_secret=settings.razorpay_webhook_secret,
        )

    async def account(self, person: Person, tag: str) -> tuple[CurrentUser, str, str]:
        email, password = f"{person.local}{tag}@example.com", account_password()
        profile = await self.auth.register(email, password, person.name, SOURCE + tag)
        await self.auth.verify_email(self.inbox.tokens.pop(email), SOURCE + tag)
        roles: tuple[str, ...] = ("bidder",)
        if person.role:
            await self.users.change_role(email, person.role, grant=True)
            roles += (person.role,)
        return CurrentUser(profile.id, roles, uuid4()), email, password

    async def listing(
        self,
        seller: CurrentUser,
        lot: Lot,
        start: datetime,
        end: datetime,
        soft_close_s: tuple[int, int] = (60, 120),  # FR-14 defaults
        publish: bool = True,
    ) -> UUID:
        async with self.sessions() as session:
            category = await CategoryRepository(session).get_by_slug(lot.category)
        assert category is not None, "seed-categories made the default tree"
        view = await self.auctions.create(
            seller,
            {
                "title": lot.title,
                "description": lot.description,
                "category_id": category.id,
                "starting_price": lot.starting_price,
                "reserve_price": lot.reserve_price,
                "min_increment": lot.min_increment,
                "start_time": start,
                "end_time": end,
                "antisnipe_threshold_s": soft_close_s[0],
                "antisnipe_extension_s": soft_close_s[1],
            },
        )
        auction_id = UUID(view["id"])
        await self.auctions.add_image(seller, auction_id, _file(IMAGES / lot.image))
        if publish:
            await self.auctions.publish(seller, auction_id)
        return auction_id

    async def wait_for(self, ids: Iterable[UUID], statuses: tuple[str, ...], by: datetime) -> None:
        """Polls Postgres until the scheduler and the close task have moved every auction."""
        while True:
            async with self.sessions() as session:
                auctions = [await AuctionRepository(session).get(i) for i in ids]
            now = [a.status if a else "missing" for a in auctions]
            if all(s in statuses for s in now):
                return
            if self.clock.now() > by:
                raise AppError(
                    "SEED_TIMEOUT",
                    f"waited for {'/'.join(statuses)}, still {now}; are beat and worker up?",
                    500,
                )
            await asyncio.sleep(1)

    async def bid(self, bidder: CurrentUser, auction_id: UUID, amount: int) -> None:
        """The REST endpoint's path: the Lua script decides, then the events are published."""
        result = await self.bids.place_bid(auction_id, bidder.id, amount, uuid4().hex)
        if not result.accepted:
            raise AppError("SEED_BID_REFUSED", f"a bid of {rupees(amount)}: {result.code}", 500)
        await live_events.publish_bid_events(
            self.redis, auction_id, bidder.id, result, self.bids.record_outbid
        )

    async def pay(self, winner: CurrentUser, auction_id: UUID) -> str:
        """A test-mode order, then the payment.captured webhook Razorpay would send for it."""
        secret = settings.razorpay_webhook_secret
        if not secret:
            return "payment due (RAZORPAY_WEBHOOK_SECRET is not set)"
        try:
            order = await self.payments.start(winner, auction_id)
        except AppError as exc:
            return f"payment due (no test order: {exc.code})"
        entity = {
            "id": f"pay_{secrets.token_hex(7)}",
            "order_id": order["order_id"],
            "amount": order["amount"],
            "currency": order["currency"],
            "status": "captured",
        }
        event = {"event": "payment.captured", "payload": {"payment": {"entity": entity}}}
        raw = json.dumps({"entity": "event", **event}).encode()
        signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        await self.payments.webhook(raw, signature, None, SOURCE)
        return (await settlement_view(self.sessions, winner, auction_id))["status"]


def credentials(
    accounts: list[tuple[str, str, str, str]],
    lots: list[tuple[str, str, str, datetime, str]],
    written: datetime,
) -> str:
    """The local credentials file: every account as (role, name, email, password), the six lots
    as (key, title, state, end time, link) with the end in IST, and where to open them."""

    def ist(at: datetime) -> str:
        return at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S IST")

    def cell(value: str) -> str:
        return value.replace("|", "\\|")

    return "\n".join(
        [
            "# Demo credentials",
            "",
            f"Written by seed-demo at {ist(written)}. Local only, outside the repository: never "
            "commit or share this file.",
            "",
            "## Accounts",
            "",
            "| Role | Name | Email | Password |",
            "|---|---|---|---|",
            *[f"| {r} | {n} | {e} | {cell(p)} |" for r, n, e, p in accounts],
            "",
            "## Lots",
            "",
            "| Lot | Title | State | Ends (IST) | Link |",
            "|---|---|---|---|---|",
            *[f"| {k} | {cell(t)} | {cell(s)} | {ist(at)} | {u} |" for k, t, s, at, u in lots],
            "",
            "## URLs",
            "",
            f"- The app: {WEB}",
            f"- Mailpit, every email the platform sends: {MAILPIT}",
            "",
        ]
    )


def write_private(path: Path, text: str) -> None:
    """Owner-only where the filesystem allows it (a Windows bind mount ignores the mode)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(text)


def _link(key: str, auction_id: UUID) -> str:
    return f"{WEB}/sell/{auction_id}" if key == "f" else f"{WEB}/auctions/{auction_id}"


def _print_accounts(people: dict[Person, tuple[CurrentUser, str, str]]) -> None:
    if settings.demo_accounts_password.get_secret_value():
        _say(f"\nDemo set ready. Every account signs in with the {FROM_ENV}.\n")
    else:
        _say("\nDemo set ready. The passwords are shown once, here, and in the credentials file.\n")
    for person, (user, email, password) in people.items():
        _say(f"  {person.name:<11} {email:<28} {shown(password)}  {', '.join(user.roles)}")


async def seed(
    sessions: async_sessionmaker[AsyncSession],
    redis: Redis,
    users: UserService,
    *,
    fresh: bool,
    live_minutes: int = 10,
    starts_in: int = 3,
) -> int:
    s = _Seeder(sessions, redis, users)
    tag = f".{secrets.token_hex(2)}" if fresh else ""
    async with sessions() as session:
        if not fresh and await UserRepository(session).get_by_email(MEERA.local + "@example.com"):
            print(
                f"error: a demo set exists ({MEERA.local}@example.com); run with --fresh for "
                "another set (nothing is deleted)",
                file=sys.stderr,
            )
            return 1

    created = await s.catalog.seed_defaults()
    _say(f"categories: the default tree is there ({created} created now)")
    people: dict[Person, tuple[CurrentUser, str, str]] = {}
    for person in (MEERA, ASHA, RAVI, ADMIN):
        people[person] = await s.account(person, tag)
        _say(f"account: {person.name} <{people[person][1]}>, verified, {people[person][0].roles}")
    meera = people[MEERA][0]
    ids: dict[str, UUID] = {}

    now = s.clock.now()
    ids["f"] = await s.listing(
        meera, LOTS["f"], now + timedelta(days=1), now + timedelta(days=4), publish=False
    )
    _say("f) draft created with its photo, starting tomorrow if published")

    # c, d and e: short auctions inside the command, closed by the real scheduler and close task
    start = s.clock.now() + timedelta(seconds=20)
    end = start + timedelta(seconds=40)
    for key in ("c", "d", "e"):
        ids[key] = await s.listing(meera, LOTS[key], start, end, soft_close_s=(10, 10))
    _say(f"c, d, e) published: live at {start:%H:%M:%S}, ending {end:%H:%M:%S} UTC")
    short = [ids[k] for k in ("c", "d", "e")]
    await s.wait_for(short, ("live",), by=start + timedelta(seconds=30))
    for key, bids in BIDS.items():
        for person, amount in bids:
            await s.bid(people[person][0], ids[key], amount)
        _say(f"{key}) {len(bids)} bids accepted, highest {rupees(bids[-1][1])}")
    await s.wait_for(short, ("closed_sold", "closed_no_sale"), by=end + timedelta(seconds=120))
    _say("c, d, e) closed by the close task")
    paid = await s.pay(people[RAVI][0], ids["d"])
    _say(f"d) {paid}")

    # a and b last, so their clocks run from the end of the seed
    start = s.clock.now() + timedelta(seconds=10)
    end = start + timedelta(minutes=live_minutes)
    ids["a"] = await s.listing(meera, LOTS["a"], start, end, soft_close_s=(20, 20))
    await s.wait_for([ids["a"]], ("live",), by=start + timedelta(seconds=30))
    _say(f"a) live, ending {end:%H:%M:%S} UTC, no bids")
    b_start = s.clock.now() + timedelta(minutes=starts_in)
    ids["b"] = await s.listing(meera, LOTS["b"], b_start, b_start + timedelta(hours=1))
    _say(f"b) scheduled, going live at {b_start:%H:%M:%S} UTC")

    c_view = await settlement_view(sessions, people[ASHA][0], ids["c"])
    states = {
        "a": f"live, ends in about {live_minutes} min (soft close 20 s, extension 20 s), no bids",
        "b": f"scheduled, goes live in about {starts_in} min",
        "c": f"sold to {c_view['winner']['full_name']} at {rupees(c_view['final_price'])}, "
        f"{c_view['status'].replace('_', ' ')}",
        "d": f"sold to {RAVI.name} at {rupees(BIDS['d'][-1][1])}, {paid.replace('_', ' ')}",
        "e": f"not sold, reserve not met (highest {rupees(BIDS['e'][-1][1])})",
        "f": f"draft, {MEERA.name}'s to publish",
    }
    async with sessions() as session:
        stored = await AuctionRepository(session).get_many(list(ids.values()))
    accounts = [
        (person.role or "bidder", person.name, email, password)
        for person, (_, email, password) in people.items()
    ]
    lots = [
        (key, LOTS[key].title, states[key], stored[ids[key]].end_time, _link(key, ids[key]))
        for key in "abcdef"
    ]
    write_private(CREDENTIALS, credentials(accounts, lots, s.clock.now()))
    _print_accounts(people)
    _say("")
    for key in "abcdef":
        _say(f"  {key}) {LOTS[key].title}\n     {states[key]}\n     {_link(key, ids[key])}")
    _say(
        f"\nCredentials written to {CREDENTIALS}, on the host DEMO_PRIVATE_DIR "
        "(../auction-private by default), outside the repository."
    )
    return 0
