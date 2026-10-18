"""Pure ASGI middleware: correlation ID per request and one log line per request (NFR-32).

Pure ASGI (not BaseHTTPMiddleware) so Phase 5 can reuse it for WebSocket events.
"""

import logging
import time
from uuid import uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import request_id_var

log = logging.getLogger("request")
HEADER = b"x-request-id"


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(HEADER)
        request_id = incoming.decode("latin-1") if incoming else uuid4().hex
        token = request_id_var.set(request_id)
        status = 0
        failed = False  # an exception escaped: the outer error handler answers 500
        start = time.perf_counter()

        async def send_with_header(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message["headers"] = [*message.get("headers", []), (HEADER, request_id.encode())]
            await send(message)

        try:
            if scope["type"] == "websocket":
                # shortcut: no response header on websocket, Phase 5 adds per-event logging
                await self.app(scope, receive, send)
            else:
                await self.app(scope, receive, send_with_header)
        except BaseException:
            failed = True
            raise
        finally:
            if scope["type"] == "http" and (status >= 500 or failed):
                metrics = getattr(scope["app"].state, "metrics", None) if "app" in scope else None
                if metrics is not None:
                    metrics.record_5xx()  # FR-81 error rate, fire-and-forget
            fields = {
                "method": scope.get("method", scope["type"]),
                "path": scope["path"],
                "status": status,
                "duration_ms": round((time.perf_counter() - start) * 1000, 2),
            }
            log.info("request", extra={"fields": fields})
            request_id_var.reset(token)
