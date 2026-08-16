"""Centralized logging configuration with request-id correlation.

Call ``configure_logging()`` once at process startup. Log records gain a
``request_id`` field (set per-request by the API middleware via
``set_request_id``) so log lines can be correlated to a single HTTP request.
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

from .config import get_settings

# Holds the current request id for the active context (one per request/task).
_request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s [req=%(request_id)s] %(message)s"
_configured = False


class _RequestIdFilter(logging.Filter):
    """Inject the active request id into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_var.get()
        return True


def set_request_id(request_id: str | None = None) -> str:
    """Set (or generate) the request id for the current context; returns it."""
    rid = request_id or uuid.uuid4().hex[:12]
    _request_id_var.set(rid)
    return rid


def get_request_id() -> str:
    return _request_id_var.get()


def configure_logging(level: str | None = None) -> None:
    """Configure the root logger once. Idempotent."""
    global _configured
    if _configured:
        return

    level_name = (level or get_settings().log_level or "INFO").upper()
    root = logging.getLogger()
    root.setLevel(level_name)

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    handler.addFilter(_RequestIdFilter())

    # Replace any pre-existing handlers so the request-id filter always applies.
    root.handlers = [handler]
    _configured = True
