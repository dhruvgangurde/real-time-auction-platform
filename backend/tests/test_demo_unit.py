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
