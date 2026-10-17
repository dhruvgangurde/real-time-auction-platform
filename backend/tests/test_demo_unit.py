"""The demo seed runs only on the plain dev stack: never outside development, never on the e2e
stack (docker-compose.e2e.yml sets E2E_STACK=1). It refuses before any connection is made."""

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app import cli, demo
from app.core.config import Settings, settings
from app.schemas.auth import PASSWORD_MIN


@pytest.fixture
def untouched(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Neither Redis nor the database may be reached: any use fails the test."""

    def no_redis(*_: Any) -> None:
        raise AssertionError("Redis was reached")

    def no_sessions() -> None:
        raise AssertionError("the database was reached")

    monkeypatch.setattr(cli, "create_redis", no_redis)
    return no_sessions


@pytest.mark.parametrize("argv", [["seed-demo"], ["seed-demo", "--fresh"]])
async def test_refuses_on_the_e2e_stack(
    argv: list[str],
    untouched: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(settings, "app_env", "local")
    monkeypatch.setattr(settings, "e2e_stack", True)
    assert await cli.run(argv, untouched) == 1
    assert "e2e overlay is active" in capsys.readouterr().err


@pytest.mark.parametrize("env", ["production", "staging", ""])
async def test_refuses_outside_development(
    env: str, untouched: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(settings, "app_env", env)
    monkeypatch.setattr(settings, "e2e_stack", False)
    assert await cli.run(["seed-demo", "--fresh"], untouched) == 1
    assert "only in development (APP_ENV=local)" in capsys.readouterr().err


# DEMO_ACCOUNTS_PASSWORD (.env, local demo only): when set every seeded account signs in with
# it and it is never printed; unset, each account gets a new password, printed once


class _Registrations:
    """Stands in for AuthService and UserService; keeps the password each account was given."""

    def __init__(self, inbox: Any) -> None:
        self.inbox, self.passwords = inbox, {}

    async def register(self, email: str, password: str, name: str, source: str) -> Any:
        self.passwords[email] = password
        self.inbox.tokens[email] = "verification-token"
        return SimpleNamespace(id=uuid4())

    async def verify_email(self, token: str, source: str) -> None:
        return None

    async def change_role(self, email: str, role: str, grant: bool) -> bool:
        return True


async def _seed_accounts() -> tuple[dict[str, str], dict[Any, Any]]:
    """The seed's four accounts, through _Seeder.account with its services faked."""
    seeder = object.__new__(demo._Seeder)  # only account() runs: no database, no Redis
    seeder.inbox = demo._Inbox()
    seeder.auth = seeder.users = fake = _Registrations(seeder.inbox)
    people = {
        p: await seeder.account(p, "") for p in (demo.MEERA, demo.ASHA, demo.RAVI, demo.ADMIN)
    }
    return fake.passwords, people


def test_the_password_comes_from_the_environment_and_is_masked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEMO_ACCOUNTS_PASSWORD", "FromTheEnvironment42")
    configured = Settings()
    assert configured.demo_accounts_password.get_secret_value() == "FromTheEnvironment42"
    assert "FromTheEnvironment42" not in repr(configured)
    assert "FromTheEnvironment42" not in str(configured.demo_accounts_password)


async def test_a_configured_password_is_every_accounts_and_is_never_printed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "Configured4Demo2026"
    monkeypatch.setattr(settings, "demo_accounts_password", SecretStr(secret))
    passwords, people = await _seed_accounts()
    assert len(passwords) == 4  # seller, both bidders, admin
    assert set(passwords.values()) == {secret}
    demo._print_accounts(people)
    out = capsys.readouterr()
    assert secret not in out.out + out.err
    assert all(email in out.out for email in passwords)
    assert out.out.count("password from DEMO_ACCOUNTS_PASSWORD") == 5  # the heading, 4 accounts


async def test_unset_each_account_gets_its_own_new_password_printed_once(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(settings, "demo_accounts_password", SecretStr(""))
    passwords, people = await _seed_accounts()
    assert len(set(passwords.values())) == 4  # a different one for each account
    assert all(len(password) >= PASSWORD_MIN for password in passwords.values())  # FR-2
    demo._print_accounts(people)
    out = capsys.readouterr().out
    assert all(out.count(password) == 1 for password in passwords.values())
    assert "DEMO_ACCOUNTS_PASSWORD" not in out


@pytest.mark.parametrize("value", ["tooShort9", "x" * 129])
async def test_a_configured_password_registration_would_refuse_stops_the_seed_unshown(
    value: str,
    untouched: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(settings, "app_env", "local")
    monkeypatch.setattr(settings, "e2e_stack", False)
    monkeypatch.setattr(settings, "demo_accounts_password", SecretStr(value))
    assert await cli.run(["seed-demo"], untouched) == 1  # before any connection
    err = capsys.readouterr().err
    assert "DEMO_ACCOUNTS_PASSWORD must be 10 to 128 characters (FR-2)" in err
    assert value not in err


# lot (a)'s length and lot (b)'s start: whole minutes, bounded, refused by the parser (exit 2)


def test_timing_defaults() -> None:
    args = cli._parser().parse_args(["seed-demo"])
    assert (args.live_minutes, args.starts_in) == (10, 3)


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--live-minutes", "3"),
        ("--live-minutes", "60"),
        ("--starts-in", "1"),
        ("--starts-in", "30"),
    ],
)
def test_timing_bounds_accepted(flag: str, value: str) -> None:
    args = cli._parser().parse_args(["seed-demo", "--fresh", flag, value])
    assert getattr(args, flag[2:].replace("-", "_")) == int(value)


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--live-minutes", "2"),
        ("--live-minutes", "61"),
        ("--live-minutes", "10.5"),
        ("--starts-in", "0"),
        ("--starts-in", "31"),
        ("--starts-in", "x"),
    ],
)
def test_timing_bounds_refused(flag: str, value: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as refused:
        cli._parser().parse_args(["seed-demo", flag, value])
    assert refused.value.code == 2
    assert "whole minutes between" in capsys.readouterr().err


# demo bots' pacing (brief 12.4, B2 Q4): at most N bids, never two within the minimum gap,
# all inside the bidding window, so a bot never comes near the per-user limit (10 per second)


@pytest.mark.parametrize("seed", range(25))
def test_bid_times_stay_inside_the_caps(seed: int) -> None:
    import random

    from app.workers.demo_bots import bid_times

    times = bid_times(random.Random(seed), window_s=60.0, bids=20, min_gap_s=2.0)
    assert len(times) == 20
    assert all(0 <= t < 60.0 for t in times)
    assert all(b - a >= 2.0 for a, b in zip(times, times[1:], strict=False))


def test_bid_times_never_exceed_what_the_gap_allows() -> None:
    import random

    from app.workers.demo_bots import bid_times

    times = bid_times(random.Random(1), window_s=10.0, bids=20, min_gap_s=2.0)
    assert len(times) == 5  # a 10 s window holds five bids 2 s apart, not twenty


# the stress run's bounds cannot be raised from the environment (brief 12.4)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("demo_stress_bots", 51),
        ("demo_stress_seconds", 61),
        ("demo_stress_stop_before_end_s", 10),  # must exceed the 10 s soft close: no extension
        ("demo_stress_start_delay_s", 0),
    ],
)
def test_stress_bounds_refuse_bigger_values(name: str, value: int) -> None:
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(**{name: value})


def test_a_full_size_run_stays_inside_the_caps() -> None:
    """50 bots, 20 bids each, a 60 s window: at most 1,000 submissions, none closer than 2 s for
    one bot (10 a second is the limit), all inside the window; a late opening only shortens it."""
    import random

    from app.workers.demo_bots import bid_times

    rng = random.Random(7)
    for window in (60.0, 45.0, 12.5):
        plans = [bid_times(rng, window, 20, 2.0) for _ in range(50)]
        assert sum(len(p) for p in plans) <= 1_000
        assert all(len(p) <= 20 for p in plans)
        assert all(0 <= t < window for p in plans for t in p)
        assert all(b - a >= 2.0 for p in plans for a, b in zip(p, p[1:], strict=False))
