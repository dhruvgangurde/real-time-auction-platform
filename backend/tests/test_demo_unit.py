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
