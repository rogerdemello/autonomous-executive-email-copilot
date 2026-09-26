"""The provider seam: fetch messages and act on them.

Every backend — the in-memory :class:`~app.copilot.providers.fake.FakeProvider`,
Gmail, Microsoft Graph — implements this one interface. Reads return provider-
neutral :class:`FetchedMessage` snapshots; writes return a :class:`WriteResult`.
The SaaS layer builds an authenticated provider and hands it to the sync service;
nothing here knows about OAuth tokens, orgs, or the database.
"""

from __future__ import annotations

import functools
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FetchedMessage:
    """A provider-neutral snapshot of one fetched message.

    Carries only what an agent may legitimately see — no ground-truth labels or
    expected actions.
    """

    provider_message_id: str
    thread_id: str
    sender: str
    sender_name: str
    subject: str
    body: str
    references: list[str] = field(default_factory=list)
    received_at: str = ""


@dataclass(frozen=True)
class WriteResult:
    """Outcome of a write action against the real mailbox."""

    ok: bool
    provider_ref: str | None = None
    detail: str = ""


_F = TypeVar("_F", bound=Callable[..., "WriteResult"])


def write_guard(fn: _F) -> _F:
    """Wrap a provider write method so a failure returns ``WriteResult(ok=False)``.

    A single failing write (auth expiry, 4xx/5xx, network) must never abort a
    whole sync batch or 500 the request — it should mark just that action failed.
    Any exception raised inside the write is caught, logged, and converted into a
    ``WriteResult`` carrying the error detail. Success passes through unchanged.
    """

    @functools.wraps(fn)
    def wrapper(self, *args: object, **kwargs: object) -> WriteResult:
        try:
            return fn(self, *args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - deliberate: convert to WriteResult
            logger.warning("Provider write %s failed: %s", fn.__name__, exc)
            return WriteResult(ok=False, detail=str(exc))

    return wrapper  # type: ignore[return-value]


class MailProvider(ABC):
    """Authenticated read/write access to one connected mailbox."""

    # -- read ---------------------------------------------------------------
    @abstractmethod
    def fetch_messages(self, folder: str = "INBOX", limit: int = 25) -> list[FetchedMessage]:
        """Fetch up to ``limit`` messages from ``folder`` as snapshots."""
        ...

    # -- write --------------------------------------------------------------
    @abstractmethod
    def send_reply(self, provider_message_id: str, body: str) -> WriteResult:
        """Send a reply to the given message."""
        ...

    @abstractmethod
    def create_escalation_draft(
        self, provider_message_id: str, body: str, *, to: str
    ) -> WriteResult:
        """Draft a hand-off of this message to ``to``, for a human to send.

        ``to`` is required, and that is the whole point of the signature. This
        used to be ``create_draft(provider_message_id, body)``, which each
        provider implemented as a *reply* — so the draft an escalation produced
        was addressed to the outside party who wrote in, carrying a body that
        said "Escalating to legal_team". The entire affordance of a draft is
        that you press Send. Naming the recipient in the signature makes the
        wrong recipient impossible to reach by accident.

        It is a forward, not a reply: the original travels with it, and it does
        not sit in the customer's thread where a stray Send would go outward.
        """
        ...

    @abstractmethod
    def add_label(self, provider_message_id: str, label: str) -> WriteResult:
        """Apply a label/category to the message."""
        ...

    @abstractmethod
    def archive(self, provider_message_id: str) -> WriteResult:
        """Archive/move the message out of the inbox."""
        ...
