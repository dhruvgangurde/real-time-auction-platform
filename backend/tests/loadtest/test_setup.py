"""The load test's set-up (Phase 11, loadtest/README.md): development only; load-test accounts
created once that nobody can sign in as; their sessions minted as the demo bots' are, so the
tokens pass the real authentication; a published lot through the lifecycle service; and the
plan, tokens included, written to a file, never to the terminal."""

import json
from datetime import timedelta
from typing import Any

import asyncpg
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import cli
from app.core.config import settings
from app.loadtest import setup
from app.services.auth import AuthService
from tests.conftest import TEST_REDIS_URL


@pytest.mark.parametrize("command", ["loadtest-setup", "loadtest-watch", "loadtest-report"])
@pytest.mark.parametrize(
    ("env", "e2e", "said"),
    [("production", False, "only in development"), ("local", True, "e2e overlay is active")],
)
async def test_refused_outside_the_dev_stack_before_any_connection(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
    env: str,
    e2e: bool,
    said: str,
) -> None:
    """The watcher turns Redis's slow log on for every command: never outside development."""
    monkeypatch.setattr(settings, "app_env", env)
    monkeypatch.setattr(settings, "e2e_stack", e2e)
    monkeypatch.setattr(cli, "create_redis", lambda *_: pytest.fail("Redis was reached"))
    assert await cli.run([command], lambda: pytest.fail("the database was reached")) == 1
    assert said in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["--bidders", "0"],
        ["--bidders", "501"],
        ["--live", "59"],
        ["--starts-in", "5"],
        ["--starts-in", "300", "--live", "600"],  # would outlive its 15-minute tokens
    ],
)
async def test_refused_plans(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "create_redis", lambda *_: pytest.fail("Redis was reached"))
    with pytest.raises(SystemExit) as refused:
        await cli.run(["loadtest-setup", *argv], lambda: pytest.fail("the database was reached"))
    assert refused.value.code == 2


async def test_setup_makes_a_plan_k6_can_run(
    sessions: async_sessionmaker[AsyncSession],
    redis: Redis,
    db: asyncpg.Connection,
    auth_service: AuthService,
    storage: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "media_root", str(storage.root))
    monkeypatch.setattr(settings, "rate_limit_auction_create", "1000/3600")
    plan = await setup(sessions, redis, bidders=3, starts_in_s=30, live_s=120)
    again = await setup(sessions, redis, bidders=3, starts_in_s=30, live_s=120)
    assert [b["id"] for b in again["bidders"]] == [b["id"] for b in plan["bidders"]]  # reused
    rows = await db.fetch(
        "SELECT email, status, is_demo, password_hash FROM users "
        "WHERE email LIKE 'loadtest-%@loadtest.invalid' ORDER BY email"
    )
    assert [r["email"] for r in rows][:4] == [
        "loadtest-001@loadtest.invalid",
        "loadtest-002@loadtest.invalid",
        "loadtest-003@loadtest.invalid",
        "loadtest-seller@loadtest.invalid",
    ]
    assert {(r["status"], r["is_demo"], r["password_hash"]) for r in rows} == {
        ("active", False, "!loadtest")  # real users, not demo ones, that nobody can sign in as
    }
    for bidder in plan["bidders"]:
        user = await auth_service.authenticate(bidder["token"])  # the real check, as a socket does
        assert (str(user.id), user.roles) == (bidder["id"], ("bidder",))
    lot = await db.fetchrow(
        "SELECT status, is_demo, start_time, end_time, antisnipe_threshold_s, "
        "antisnipe_extension_s, starting_price, min_increment FROM auctions WHERE id = $1::uuid",
        plan["auction_id"],
    )
    assert (lot["status"], lot["is_demo"]) == ("scheduled", False)
    assert lot["end_time"] - lot["start_time"] == timedelta(seconds=120)
    assert (lot["antisnipe_threshold_s"], lot["antisnipe_extension_s"]) == (10, 10)
    assert (plan["start_price"], plan["min_increment"]) == (
        lot["starting_price"],
        lot["min_increment"],
    )
    assert plan["expires_at_ms"] - plan["minted_at_ms"] == settings.access_token_ttl_s * 1000


async def test_the_cli_writes_the_tokens_to_the_file_only(
    sessions: async_sessionmaker[AsyncSession],
    storage: Any,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(settings, "redis_url", TEST_REDIS_URL)
    monkeypatch.setattr(settings, "media_root", str(storage.root))
    monkeypatch.setattr(settings, "rate_limit_auction_create", "1000/3600")
    out = tmp_path / "run" / "plan.json"
    assert await cli.run(["loadtest-setup", "--bidders", "2", "--out", str(out)], sessions) == 0
    plan = json.loads(out.read_text(encoding="utf-8"))
    printed = capsys.readouterr().out
    assert plan["auction_id"] in printed and "2 bidders" in printed
    assert all(b["token"] not in printed for b in plan["bidders"])  # never on the terminal
