"""Operator commands (D6). Roles are not self-service (FR-9), so they are granted here:

    python -m app.cli grant-role <email> <role>
    python -m app.cli revoke-role <email> <role>
    python -m app.cli reinstate <email> --reason "<why>"   (recovery, e.g. no active admin)
    python -m app.cli seed-categories
    python -m app.cli seed-demo [--fresh] [--live-minutes N] [--starts-in M]   (app/demo.py)
    python -m app.cli loadtest-setup [--bidders N] [--starts-in S] [--live S] [--out PATH]
    python -m app.cli loadtest-watch [--dir PATH]
    python -m app.cli loadtest-report [--dir PATH] [--cited]
        (development only, from the loadtest-tools service, as loadtest/run.sh runs them:
        app/loadtest.py)

Every change is written to audit_log in the same transaction (actor NULL, reason 'cli'). A
revoke also revokes every session of the user, so the role is gone from the next request on.
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import demo, loadtest
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
    seed_demo = commands.add_parser("seed-demo")
    seed_demo.add_argument(
        "--fresh", action="store_true", help="another demo set beside the old one (none deleted)"
    )
    seed_demo.add_argument(
        "--live-minutes", type=_minutes(3, 60), default=10, help="how long lot (a) is live"
    )
    seed_demo.add_argument(
        "--starts-in", type=_minutes(1, 30), default=3, help="minutes until lot (b) goes live"
    )
    setup = commands.add_parser("loadtest-setup")
    setup.add_argument("--bidders", type=_bounded(1, 500), default=110)
    setup.add_argument("--starts-in", type=_bounded(10, 600), default=45, help="seconds")
    setup.add_argument("--live", type=_bounded(60, 900), default=240, help="seconds")
    setup.add_argument("--out", default="/loadtest/.run/plan.json", help="gitignored (tokens)")
    watch = commands.add_parser("loadtest-watch")
    watch.add_argument("--dir", default="/loadtest/.run", help="reads plan.json, writes watch.json")
    report = commands.add_parser("loadtest-report")
    report.add_argument(
        "--dir", default="/loadtest", help="reads .run/, writes results/<date>.json, RESULTS.md"
    )
    report.add_argument("--cited", action="store_true", help="only print the cited tests' ids")
    reinstate = commands.add_parser("reinstate")
    reinstate.add_argument("email")
    reinstate.add_argument("--reason", required=True, type=_reason)
    return parser


def _reason(value: str) -> str:
    """Every operator action states a reason (BR-12)."""
    if not value.strip():
        raise argparse.ArgumentTypeError("a reason is required")
    return value.strip()


def _bounded(low: int, high: int) -> Callable[[str], int]:
    def parse(value: str) -> int:
        if not (value.isdigit() and low <= int(value) <= high):
            raise argparse.ArgumentTypeError(f"a whole number between {low} and {high}")
        return int(value)

    return parse


def _minutes(low: int, high: int) -> Callable[[str], int]:
    def parse(value: str) -> int:
        if not (value.isdigit() and low <= int(value) <= high):
            raise argparse.ArgumentTypeError(f"whole minutes between {low} and {high}")
        return int(value)

    return parse


async def run(argv: list[str], sessions: async_sessionmaker[AsyncSession]) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "loadtest-setup":
        if loadtest.longest_run_s(args.starts_in, args.live) > settings.access_token_ttl_s:
            parser.error("the run would outlive its tokens (15 minutes): shorten it")
    if args.command.startswith("loadtest-") and (refused := loadtest.refusal()) is not None:
        print(f"error: {refused}", file=sys.stderr)  # before any connection is made
        return 1
    if args.command == "loadtest-report" and args.cited:
        print(" ".join(node for nodes in loadtest.CITED.values() for node in nodes))
        return 0
    if args.command == "loadtest-watch":
        return await _loadtest_watch(sessions, Path(args.dir))
    if args.command == "loadtest-report":
        return await _loadtest_report(sessions, Path(args.dir))
    if args.command == "seed-demo" and (refused := demo.refusal()) is not None:
        print(f"error: {refused}", file=sys.stderr)  # before any connection is made
        return 1
    redis = create_redis(settings.redis_url)  # denylist and session_revoked on a role revoke
    try:
        if args.command == "loadtest-setup":
            plan = await loadtest.setup(
                sessions, redis, bidders=args.bidders, starts_in_s=args.starts_in, live_s=args.live
            )
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(plan), encoding="utf-8")
            print(
                f"plan written to {out}: auction {plan['auction_id']}, {args.bidders} bidders, "
                f"opening in {args.starts_in} s for {args.live} s; tokens until "
                f"{plan['expires_at_ms']} ms (they are in the file only)"
            )
            return 0
        if args.command == "seed-categories":
            catalog = CatalogService(sessions, redis, LocalStorage(settings.media_root))
            print(f"categories created: {await catalog.seed_defaults()}")
            return 0

        async def notify_revoked(user_id: UUID, sid: UUID | None) -> None:
            await publish_session_revoked(redis, user_id, sid)

        # the same revocation path as the admin API: denylist with its fallbacks, sockets closed
        users = UserService(sessions, SystemClock(), Denylist(redis, enqueue), notify_revoked)
        if args.command == "seed-demo":
            return await demo.seed(
                sessions,
                redis,
                users,
                fresh=args.fresh,
                live_minutes=args.live_minutes,
                starts_in=args.starts_in,
            )
        if args.command == "reinstate":
            await users.reinstate_user(None, args.email, f"cli: {args.reason}")
            print(f"reinstated {args.email}; they can log in again")
            return 0
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


async def _loadtest_watch(sessions: async_sessionmaker[AsyncSession], run: Path) -> int:
    plan = json.loads((run / "plan.json").read_text(encoding="utf-8"))
    # the last possible end (every extension, FR-54), then closing and settling
    last = plan["end_ms"] / 1000 + 10 * plan["soft_close_s"] + loadtest.SETTLE_MARGIN_S
    # own clients: a slow-log read is larger than anything on the bid path
    redis = Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=5)
    broker = Redis.from_url(settings.celery_broker_url, decode_responses=True, socket_timeout=5)
    try:
        watched = await loadtest.watch(sessions, redis, broker, UUID(plan["auction_id"]), last)
    finally:
        await redis.aclose()
        await broker.aclose()
    (run / "watch.json").write_text(json.dumps(watched), encoding="utf-8")
    print(
        f"watch written to {run / 'watch.json'}: settled {watched['settled']}, "
        f"{len(watched['arbitration_us'])} arbitrations, {len(watched['persistence_ms'])} bid rows"
    )
    return 0 if watched["settled"] else 1


async def _loadtest_report(sessions: async_sessionmaker[AsyncSession], base: Path) -> int:
    evidence = await loadtest.evidence(sessions, base / ".run")
    doc = loadtest.document(evidence, loadtest.machine(base / ".run"))
    out = base / "results" / f"{doc['date']}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (base / "RESULTS.md").write_text(loadtest.markdown(doc), encoding="utf-8")
    print(f"written: {out}, {base / 'RESULTS.md'}")
    for r in doc["requirements"]:
        print(f"{r['id']:<7} {loadtest.VERDICT[r['pass']]:<14} {r['measured']}")
    return 0


def main() -> None:
    from app.core.db import SessionLocal

    sys.exit(asyncio.run(run(sys.argv[1:], SessionLocal)))


if __name__ == "__main__":
    main()
