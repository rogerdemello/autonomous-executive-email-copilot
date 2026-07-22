"""Transactional email — pluggable, with a zero-config console default.

Password reset and member invites need to send email. To keep local dev and
tests dependency-free, the default provider just **logs** the message
(``ConsoleEmailSender``); setting ``EMAIL_PROVIDER=smtp`` plus the ``SMTP_*``
settings switches to real delivery via the stdlib ``smtplib``. Tests use
``MemorySender`` (or monkeypatch :func:`get_email_sender`) to assert what would
have been sent — no network.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..config import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    body: str


class EmailSender(ABC):
    @abstractmethod
    def send(self, message: EmailMessage) -> None:
        """Deliver (or record) an email. Must not raise into the request path."""
        ...


class ConsoleEmailSender(EmailSender):
    """Logs the email instead of sending it. The safe default for dev/tests."""

    def send(self, message: EmailMessage) -> None:
        logger.info(
            "EMAIL (console) to=%s subject=%s\n%s",
            message.to,
            message.subject,
            message.body,
        )


class MemorySender(EmailSender):
    """Collects sent messages in memory for assertions in tests."""

    def __init__(self) -> None:
        self.outbox: list[EmailMessage] = []

    def send(self, message: EmailMessage) -> None:
        self.outbox.append(message)


class SMTPEmailSender(EmailSender):
    """Sends via SMTP using the stdlib. Best-effort; logs and swallows failures."""

    def send(self, message: EmailMessage) -> None:
        import smtplib
        from email.message import EmailMessage as MimeMessage

        settings = get_settings()
        if not settings.smtp_host:
            logger.error("EMAIL_PROVIDER=smtp but SMTP_HOST is not set; dropping email")
            return
        mime = MimeMessage()
        mime["From"] = settings.email_from
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime.set_content(message.body)
        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
                if settings.smtp_starttls:
                    smtp.starttls()
                if settings.smtp_user:
                    smtp.login(settings.smtp_user, settings.smtp_password or "")
                smtp.send_message(mime)
        except Exception:  # pragma: no cover - network/SMTP failure path
            logger.warning("Failed to send email via SMTP to %s", message.to, exc_info=True)


def get_email_sender() -> EmailSender:
    """Return the configured email sender (console by default)."""
    provider = (get_settings().email_provider or "console").lower()
    if provider == "smtp":
        return SMTPEmailSender()
    return ConsoleEmailSender()


def send_email(to: str, subject: str, body: str) -> None:
    """Convenience: build + send in one call via the configured sender."""
    get_email_sender().send(EmailMessage(to=to, subject=subject, body=body))
