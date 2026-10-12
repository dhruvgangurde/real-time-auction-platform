"""Outbound email (SI-4; the provider is TBD-1). Every sender has the same `send(to, subject,
body)`; which one runs is configuration only:

- ConsoleEmailSender (EMAIL_BACKEND=console, dev only) logs the whole message, including links
  that carry raw tokens; check_startup_config() refuses it outside APP_ENV=local.
- SmtpEmailSender (EMAIL_BACKEND=smtp) delivers through any SMTP server: Mailpit in Docker
  Compose for development, a provider's SMTP endpoint in production.
- QueuedEmailSender hands the message to the `send_email` Celery task, so a request never waits
  for SMTP and its timing does not reveal whether an address is registered. The task retries.
"""

import asyncio
import logging
import smtplib
from collections.abc import Callable
from email.message import EmailMessage
from typing import Protocol

from app.core.config import settings

log = logging.getLogger("email")


class EmailSender(Protocol):
    async def send(self, to: str, subject: str, body: str) -> None: ...


class ConsoleEmailSender:
    async def send(self, to: str, subject: str, body: str) -> None:
        log.info(
            "[dev-only] email",
            extra={"fields": {"to": to, "subject": subject, "body": body}},
        )


class SmtpEmailSender:
    def __init__(
        self,
        host: str,
        port: int,
        *,
        username: str,
        password: str,
        starttls: bool,
        sender: str,
        timeout_s: float,
    ) -> None:
        self._host, self._port = host, port
        self._username, self._password, self._starttls = username, password, starttls
        self._sender, self._timeout = sender, timeout_s

    async def send(self, to: str, subject: str, body: str) -> None:
        await asyncio.to_thread(self._send, to, subject, body)

    def _send(self, to: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self._sender
        message["To"] = to
        message["Subject"] = " ".join(subject.split())  # no header injection through titles
        message.set_content(body)
        with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as smtp:
            if self._starttls:
                smtp.starttls()
            if self._username:
                smtp.login(self._username, self._password)
            smtp.send_message(message)


class QueuedEmailSender:
    def __init__(self, enqueue: Callable[..., None]) -> None:
        self._enqueue = enqueue

    async def send(self, to: str, subject: str, body: str) -> None:
        # the body can carry a raw verification or reset token: never logged on failure
        self._enqueue("send_email", [to, subject, body], log_args=False)


def email_sender_from_settings() -> ConsoleEmailSender | SmtpEmailSender:
    """The sender that actually delivers (the worker side, and console in local dev)."""
    if settings.email_backend == "console":
        return ConsoleEmailSender()
    return SmtpEmailSender(
        settings.smtp_host,
        settings.smtp_port,
        username=settings.smtp_username,
        password=settings.smtp_password,
        starttls=settings.smtp_starttls,
        sender=settings.email_from,
        timeout_s=settings.smtp_timeout_s,
    )
