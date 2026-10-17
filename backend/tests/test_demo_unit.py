"""The demo seed runs only on the plain dev stack: never outside development, never on the e2e
stack (docker-compose.e2e.yml sets E2E_STACK=1). It refuses before any connection is made."""

from typing import Any

import pytest

from app import cli
from app.core.config import settings


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
