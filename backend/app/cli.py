"""Operator commands (D6). Roles are not self-service (FR-9), so they are granted here:

    python -m app.cli grant-role <email> <role>
    python -m app.cli revoke-role <email> <role>
    python -m app.cli seed-categories

Every change is written to audit_log in the same transaction (actor NULL, reason 'cli'). A
revoke also revokes every session of the user, so the role is gone from the next request on.
"""

import argparse
import asyncio
import sys
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import SystemClock
from app.core.config import settings
from app.core.errors import AppError
from app.core.redis import create_redis
from app.core.storage import LocalStorage
from app.models.user import ROLES
from app.services.catalog import CatalogService
from app.services.denylist import Denylist
from app.services.live_events import publish_session_revoked
from app.services.users import UserService
from app.workers.dispatch import enqueue


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("grant-role", "revoke-role"):
        command = commands.add_parser(name)
        command.add_argument("email")
        command.add_argument("role", choices=ROLES)
    commands.add_parser("seed-categories")
    return parser


async def run(argv: list[str], sessions: async_sessionmaker[AsyncSession]) -> int:
    args = _parser().parse_args(argv)
    redis = create_redis(settings.redis_url)  # denylist and session_revoked on a role revoke
    try:
        if args.command == "seed-categories":
            catalog = CatalogService(sessions, redis, LocalStorage(settings.media_root))
            print(f"categories created: {await catalog.seed_defaults()}")
            return 0

        async def notify_revoked(user_id: UUID, sid: UUID | None) -> None:
            await publish_session_revoked(redis, user_id, sid)

        # the same revocation path as the admin API: denylist with its fallbacks, sockets closed
        users = UserService(sessions, SystemClock(), Denylist(redis, enqueue), notify_revoked)
        grant = args.command == "grant-role"
        changed = await users.change_role(args.email, args.role, grant=grant)
        if not changed:
            print(
                f"no change: {args.email} {'already has' if grant else 'does not have'} "
                f"the {args.role} role"
            )
            return 0
        if grant:
            print(
                f"granted {args.role} for {args.email}; "
                "they must log in again (roles are carried in the access token)"
            )
        else:
            print(
                f"revoked {args.role} for {args.email}; every session was revoked, "
                "so they must log in again"
            )
        return 0
    except AppError as exc:
        print(f"error: {exc.code}: {exc.message}", file=sys.stderr)
        return 1
    finally:
        await redis.aclose()


def main() -> None:
    from app.core.db import SessionLocal

    sys.exit(asyncio.run(run(sys.argv[1:], SessionLocal)))


if __name__ == "__main__":
    main()
