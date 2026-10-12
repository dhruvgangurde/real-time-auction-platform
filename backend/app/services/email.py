"""Outbound email. TBD-1 (provider) is open, so only a console backend exists.

The console backend logs the whole message, including links that carry raw tokens. It is
dev-only: check_startup_config() refuses it outside APP_ENV=local.
"""

import logging
from typing import Protocol

log = logging.getLogger("email")


class EmailSender(Protocol):
    async def send(self, to: str, subject: str, body: str) -> None: ...


class ConsoleEmailSender:
    async def send(self, to: str, subject: str, body: str) -> None:
        log.info(
            "[dev-only] email",
            extra={"fields": {"to": to, "subject": subject, "body": body}},
        )
