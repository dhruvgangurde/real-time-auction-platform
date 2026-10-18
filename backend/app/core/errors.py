"""Single error envelope for every response (CI-2): {"error": {"code", "message"}}, plus an
optional "fields" object of per-field messages for validation failures (FR-12)."""

import logging
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)


class AppError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status: int = 400,
        headers: dict[str, str] | None = None,
        fields: dict[str, str] | None = None,
        after: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.headers = headers or {}
        self.fields = fields
        self.after = after  # runs once the error response is sent (a count, never the answer)


def envelope(
    code: str,
    message: str,
    status: int,
    headers: dict[str, str] | None = None,
    fields: dict[str, str] | None = None,
) -> JSONResponse:
    error: dict[str, object] = {"code": code, "message": message}
    if fields:
        error["fields"] = fields
    return JSONResponse({"error": error}, status_code=status, headers=headers)


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    response = envelope(exc.code, exc.message, exc.status, exc.headers, exc.fields)
    if exc.after is not None:
        response.background = BackgroundTask(exc.after)
    return response


async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    fields: dict[str, str] = {}
    for e in errors:
        loc = [str(p) for p in e.get("loc", ())]
        # drop the source ("body", "query", "path") so the key is the field name
        fields.setdefault(".".join(loc[1:]) or ".".join(loc) or "request", str(e.get("msg")))
    message = str(errors[0].get("msg", "invalid request")) if errors else "invalid request"
    return envelope("VALIDATION_ERROR", message, 422, fields=fields)


async def http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = "NOT_FOUND" if exc.status_code == 404 else f"HTTP_{exc.status_code}"
    return envelope(code, str(exc.detail), exc.status_code)


async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error")  # trace goes to the log only, never to the client
    return envelope("INTERNAL_ERROR", "internal server error", 500)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_error_handler)
