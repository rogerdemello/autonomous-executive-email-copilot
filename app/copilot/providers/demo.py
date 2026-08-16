"""The demo mailbox: a realistic executive inbox with no credentials and no network.

Loaded from ``data/demo/inbox.json`` so the content can be edited — reworded for
a particular audience, or swapped for an industry-specific set — without
touching code.

What is authored and what is not matters here, because a demo that fakes its
conclusions is worth nothing in front of a technical audience:

- **Not authored:** every decision. Priority, risk tag, deadline, and the choice
  between reply / escalate / defer / file are computed at request time by the
  same :class:`~app.copilot.policy.BaselinePolicy` that runs against a real
  Gmail or Microsoft 365 mailbox. Change a subject line in the JSON and the
  routing genuinely changes.
- **Authored:** the prose. The policy emits one generic sentence for every
  reply, which reads as hollow the moment anyone looks twice. ``draft_for``
  supplies a written reply per message, and ``rationale_for`` supplies the
  reviewer-facing explanation.

Writes are recorded in memory rather than sent, so approving a reply during a
demo is safe and repeatable.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from app.core.paths import DEMO_DIR

from .base import FetchedMessage, MailProvider, WriteResult

logger = logging.getLogger(__name__)

DEMO_PROVIDER_KEY = "demo"
DEMO_INBOX_FILE = DEMO_DIR / "inbox.json"


@lru_cache(maxsize=4)
def _load(path: Path) -> dict:
    """Read and cache the fixture. Cached because it is immutable at runtime."""
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def demo_account_email() -> str:
    return str(_load(DEMO_INBOX_FILE).get("account_email", "demo@example.com"))


def demo_message_count() -> int:
    return len(_load(DEMO_INBOX_FILE).get("messages", []))


class DemoProvider(MailProvider):
    """A fixed, realistic mailbox served from disk. No network, no credentials."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or DEMO_INBOX_FILE
        raw = _load(self._path)
        self._raw_messages: list[dict] = list(raw.get("messages", []))
        self.account_email: str = str(raw.get("account_email", "demo@example.com"))
        # Recorded side effects — nothing leaves the process.
        self.sent: list[dict] = []
        self.drafts: list[dict] = []
        self.labels: list[dict] = []
        self.archived: list[str] = []

    # -- read ---------------------------------------------------------------
    def fetch_messages(self, folder: str = "INBOX", limit: int = 25) -> list[FetchedMessage]:
        return [
            FetchedMessage(
                provider_message_id=m["provider_message_id"],
                thread_id=m.get("thread_id", ""),
                sender=m["sender"],
                sender_name=m.get("sender_name", ""),
                subject=m.get("subject", ""),
                body=m.get("body", ""),
                references=list(m.get("references", [])),
                received_at=m.get("received_at", ""),
            )
            for m in self._raw_messages[:limit]
        ]

    # -- authored narration -------------------------------------------------
    def draft_for(self, provider_message_id: str) -> str | None:
        """The written reply for a message, if one was authored."""
        entry = self._entry(provider_message_id)
        return (entry or {}).get("suggested_reply") or None

    def rationale_for(self, provider_message_id: str) -> list[str]:
        """Why the copilot's decision makes sense, in a reviewer's language."""
        entry = self._entry(provider_message_id)
        return list((entry or {}).get("rationale") or [])

    def _entry(self, provider_message_id: str) -> dict | None:
        for m in self._raw_messages:
            if m["provider_message_id"] == provider_message_id:
                return m
        return None

    # -- write (recorded, never sent) ---------------------------------------
    def send_reply(self, provider_message_id: str, body: str) -> WriteResult:
        self.sent.append({"message_id": provider_message_id, "body": body})
        logger.info("Demo mailbox: recorded a reply to %s (not sent)", provider_message_id)
        return WriteResult(ok=True, provider_ref=f"demo-sent-{provider_message_id}")

    def create_draft(self, provider_message_id: str, body: str) -> WriteResult:
        self.drafts.append({"message_id": provider_message_id, "body": body})
        return WriteResult(ok=True, provider_ref=f"demo-draft-{provider_message_id}")

    def add_label(self, provider_message_id: str, label: str) -> WriteResult:
        self.labels.append({"message_id": provider_message_id, "label": label})
        return WriteResult(ok=True, provider_ref=f"demo-label-{provider_message_id}-{label}")

    def archive(self, provider_message_id: str) -> WriteResult:
        self.archived.append(provider_message_id)
        return WriteResult(ok=True, provider_ref=f"demo-archived-{provider_message_id}")
