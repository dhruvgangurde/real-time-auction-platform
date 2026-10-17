"""Demo accounts (brief 12.3, decided 2026-10-13, B2 Q5).

The demo seller, the watcher and the stress bots are ordinary users flagged is_demo, created once
and reused, with an unusable password hash and a reserved .invalid address, so nobody can sign in
as them or be emailed as them. The runner signs them in the way AuthService.login does after its
password check: a session row and an access token. That skips 50 argon2 hashes (64 MiB each) and
the per-address login limit, while every socket still authenticates with a real token and meets
the same per-user limits as a person.
"""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import Clock
from app.core.config import settings
from app.core.security import issue_access_token, new_token
from app.repositories.session import SessionRepository
from app.repositories.user import UserRepository

UNUSABLE_HASH = "!demo"  # not an argon2 hash, so verify_password is always False for it
DOMAIN = "demo.invalid"  # reserved: no mail can be delivered there


@dataclass(frozen=True)
class DemoAccount:
    id: UUID
    roles: tuple[str, ...]


@dataclass(frozen=True)
class DemoCast:
    seller: DemoAccount
    watcher: DemoAccount
    bots: tuple[DemoAccount, ...]


class DemoAccounts:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], clock: Clock) -> None:
        self._sessions, self._clock = sessions, clock

    async def ensure(self, bots: int) -> DemoCast:
        """Creates whichever demo accounts are missing; never adopts a real account."""
        specs = [
            ("demo-seller", "Demo seller", ("bidder", "seller")),
            ("demo-watcher", "Demo watcher", ("bidder",)),
            *(
                (f"stress-bot-{n:02d}", f"Demo bidder {n:02d}", ("bidder",))
                for n in range(1, bots + 1)
            ),
        ]
        found: list[DemoAccount] = []
        async with self._sessions() as session, session.begin():
            users = UserRepository(session)
            for local, name, roles in specs:
                email = f"{local}@{DOMAIN}"
                user = await users.get_by_email(email)
                if user is None:
                    user = await users.create(
                        email, UNUSABLE_HASH, name, roles, status="active", is_demo=True
                    )
                elif not user.is_demo:
                    raise RuntimeError(f"{email} exists and is not a demo account")
                found.append(DemoAccount(user.id, roles))
        return DemoCast(found[0], found[1], tuple(found[2:]))

    async def sign_in(self, accounts: tuple[DemoAccount, ...]) -> dict[UUID, tuple[UUID, str]]:
        """A session and an access token per account: {user id: (session id, token)}. The
        refresh token is never used (a run ends before the access token does), so the session
        expires with the access token."""
        now = self._clock.now()
        signed: dict[UUID, tuple[UUID, str]] = {}
        async with self._sessions() as session, session.begin():
            repo = SessionRepository(session)
            for account in accounts:
                _, digest = new_token()
                expires = now + timedelta(seconds=settings.access_token_ttl_s)
                sid = await repo.create(account.id, digest, expires)
                signed[account.id] = (sid, issue_access_token(account.id, account.roles, sid, now))
        return signed

    async def sign_out_all(self) -> int:
        """Removes every demo account's session: a previous runner's at startup and each run's
        at its end, so they never pile up."""
        async with self._sessions() as session, session.begin():
            return await SessionRepository(session).delete_for_demo_users()
