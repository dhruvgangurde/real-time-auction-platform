"""Demo accounts (brief 12.3, decided 2026-10-13, B2 Q5).

The demo seller, the watcher and the stress bots are ordinary users flagged is_demo, created once
and reused, with an unusable password hash and a reserved .invalid address, so nobody can sign in
as them or be emailed as them. The runner signs them in the way AuthService.login does after its
password check: a session row and an access token. That skips 50 argon2 hashes (64 MiB each) and
the per-address login limit, while every socket still authenticates with a real token and meets
the same per-user limits as a person. Signing out is an ordinary revocation (revoked_at, then the
Redis denylist), so a token stops working at once.
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
from app.services.denylist import Denylist, sid_key

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
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], clock: Clock, denylist: Denylist
    ) -> None:
        self._sessions, self._clock, self._denylist = sessions, clock, denylist

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
        """Revokes every live demo session (a previous runner's at startup, each run's at its
        end) as any revocation is done: revoked_at in Postgres, then the denylist, so its token
        is refused at once. Sessions whose token has expired are deleted, so they never pile up.
        Returns how many were revoked."""
        now = self._clock.now()
        async with self._sessions() as session, session.begin():
            sessions = SessionRepository(session)
            revoked = await sessions.revoke_for_demo_users(now)
            await sessions.delete_expired_for_demo_users(now)
        if revoked:
            await self._denylist.revoke([sid_key(s) for s in revoked])
        return len(revoked)
