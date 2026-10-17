"""The load test's set-up (Phase 11, loadtest/README.md): `python -m app.cli loadtest-setup`, run
from the `loadtest-tools` service of docker-compose.loadtest.yml.

Development only. It creates, once, a load-test seller and N bidder accounts: real users, not
demo ones, at reserved `.invalid` addresses and with an unusable password hash, so nobody can
sign in as them. It signs the bidders in the way login does after its password check, a session
row and an access token, as the demo bots are (B2), so each k6 socket authenticates with a real
token and meets the per-user limits like anyone. It creates and publishes the load-test auction
through the real lifecycle service, and returns the plan k6 runs from, tokens included, which
the CLI writes to a gitignored file. The tokens last access_token_ttl_s (15 minutes): the run
must end before they do, which the CLI checks before it starts.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import SystemClock
from app.core.config import settings
from app.core.ratelimit import RateLimiter
from app.core.security import issue_access_token, new_token
from app.core.storage import LocalStorage
from app.repositories.category import CategoryRepository
from app.repositories.session import SessionRepository
from app.repositories.user import UserRepository
from app.services.auctions import AuctionService
from app.services.auth import CurrentUser
from app.services.catalog import CatalogService
from app.services.live_state import to_ms

DOMAIN = "loadtest.invalid"  # reserved: no mail is delivered there, and the API refuses it
UNUSABLE_HASH = "!loadtest"  # not an argon2 hash, so no password ever matches it
IMAGE = Path(__file__).resolve().parents[1] / "demo" / "images" / "bidri-pandan.jpg"  # CC0
CATEGORY = "home-and-garden"
LOT = {
    "starting_price": 1_000_000,  # INR 10,000 in paise; room for 99,000 increments (100x cap)
    "min_increment": 1_000,  # INR 10
    "reserve_price": None,
}
# the soft close of the lot: the shortest allowed, so the final minute's bids extend it by at
# most 10 x 10 s (FR-54) and the run stays inside its tokens' life
SOFT_CLOSE_S = 10
SETTLE_MARGIN_S = 120  # after the last possible end: closing, settling, the clients' last frames


def refusal() -> str | None:
    if settings.app_env != "local":
        return (
            "the load test runs only in development (APP_ENV=local), "
            f"not APP_ENV={settings.app_env}"
        )
    if settings.e2e_stack:
        return "the e2e overlay is active (E2E_STACK=1): run the load test on the dev stack"
    return None


def longest_run_s(starts_in_s: int, live_s: int) -> int:
    """From set-up to the clients' last frame, with every extension the lot can take."""
    return starts_in_s + live_s + 10 * SOFT_CLOSE_S + SETTLE_MARGIN_S


@dataclass(frozen=True)
class Account:
    id: UUID
    roles: tuple[str, ...]


async def _accounts(sessions: async_sessionmaker[AsyncSession], bidders: int) -> list[Account]:
    """The seller first, then the bidders; created when missing, never adopted from an account
    that was not made here."""
    specs = [
        ("loadtest-seller", "Load test seller", ("bidder", "seller")),
        *(
            (f"loadtest-{n:03d}", f"Load test bidder {n:03d}", ("bidder",))
            for n in range(1, bidders + 1)
        ),
    ]
    found: list[Account] = []
    async with sessions() as session, session.begin():
        users = UserRepository(session)
        for local, name, roles in specs:
            email = f"{local}@{DOMAIN}"
            user = await users.get_by_email(email)
            if user is None:
                user = await users.create(email, UNUSABLE_HASH, name, roles, status="active")
            elif user.password_hash != UNUSABLE_HASH or user.is_demo:
                raise RuntimeError(f"{email} exists and is not a load-test account")
            found.append(Account(user.id, roles))
    return found


async def _mint(
    sessions: async_sessionmaker[AsyncSession], accounts: list[Account], now: datetime
) -> dict[UUID, str]:
    """A session and an access token per account, the session expiring with the token."""
    tokens: dict[UUID, str] = {}
    async with sessions() as session, session.begin():
        repo = SessionRepository(session)
        for account in accounts:
            _, digest = new_token()  # the refresh token is never used
            expires = now + timedelta(seconds=settings.access_token_ttl_s)
            sid = await repo.create(account.id, digest, expires)
            tokens[account.id] = issue_access_token(account.id, account.roles, sid, now)
    return tokens


async def _file(path: Path) -> AsyncIterator[bytes]:
    yield path.read_bytes()


async def setup(
    sessions: async_sessionmaker[AsyncSession],
    redis: Redis,
    *,
    bidders: int,
    starts_in_s: int,
    live_s: int,
) -> dict[str, Any]:
    clock = SystemClock()
    seller, *people = await _accounts(sessions, bidders)
    await CatalogService(sessions, redis, LocalStorage("/")).seed_defaults()
    async with sessions() as session:
        category = await CategoryRepository(session).get_by_slug(CATEGORY)
    assert category is not None, "the default categories were just made sure of"
    auctions = AuctionService(
        sessions, redis, clock, RateLimiter(redis, clock), LocalStorage(settings.media_root)
    )
    now = clock.now()
    start = now + timedelta(seconds=starts_in_s)
    stamp = now.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    user = CurrentUser(seller.id, seller.roles, uuid4())
    view = await auctions.create(
        user,
        {
            **LOT,
            "title": f"Load test lot, {stamp}",
            "description": (
                "A load-test listing (Phase 11, loadtest/README.md): 100 and more simulated "
                "bidders bid on it through the real bid path.\n\n"
                "Photo of a comparable piece: Cleveland Museum of Art, 2020.206, CC0."
            ),
            "category_id": category.id,
            "start_time": start,
            "end_time": start + timedelta(seconds=live_s),
            "antisnipe_threshold_s": SOFT_CLOSE_S,
            "antisnipe_extension_s": SOFT_CLOSE_S,
        },
    )
    lot = UUID(view["id"])
    await auctions.add_image(user, lot, _file(IMAGE))
    await auctions.publish(user, lot)
    minted = clock.now()
    tokens = await _mint(sessions, people, minted)
    return {
        "auction_id": str(lot),
        "start_ms": to_ms(start),
        "end_ms": to_ms(start + timedelta(seconds=live_s)),
        "soft_close_s": SOFT_CLOSE_S,
        "start_price": LOT["starting_price"],
        "min_increment": LOT["min_increment"],
        "minted_at_ms": to_ms(minted),
        "expires_at_ms": to_ms(minted + timedelta(seconds=settings.access_token_ttl_s)),
        "bidders": [{"id": str(p.id), "token": tokens[p.id]} for p in people],
    }
