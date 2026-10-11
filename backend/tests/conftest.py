import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from app.main import app as _app


class ValidateBody(BaseModel):
    n: int


def _register_test_routes(app: FastAPI) -> None:
    @app.post("/_test/validate")
    async def validate(body: ValidateBody) -> dict[str, int]:
        return {"n": body.n}

    @app.get("/_test/boom")
    async def boom() -> None:
        raise RuntimeError("boom")


_register_test_routes(_app)  # once per test session


@pytest.fixture
async def client():
    # raise_app_exceptions=False lets the 500 handler run instead of re-raising into the test
    transport = ASGITransport(app=_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
