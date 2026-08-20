"""Read-only IMAP connector.

Selects the mailbox with ``readonly=True`` and only ever issues SEARCH/FETCH — it
cannot mark, move, or delete mail. The IMAP client is injected (already connected
and authenticated) so the connector is testable without any network.
"""

from __future__ import annotations

from email import message_from_bytes
from email.message import Message
from typing import Protocol

from .base import RawEmail, ReadOnlyEmailConnector


class ImapClient(Protocol):
    """The minimal read-only slice of imaplib.IMAP4 this connector uses."""

    def select(self, mailbox: str, readonly: bool = ...) -> tuple: ...
    def search(self, charset, *criteria) -> tuple: ...
    def fetch(self, message_set, message_parts) -> tuple: ...


def _extract_body(msg: Message) -> str:
    """Return the first text/plain part (or the payload) as a decoded string."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload is not None:
                    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        return ""
    payload = msg.get_payload(decode=True)
    if payload is None:
        return str(msg.get_payload())
    return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")


def parse_raw_email(raw_bytes: bytes, fallback_id: str) -> RawEmail:
    """Parse RFC822 bytes into a provider-neutral RawEmail snapshot."""
    msg = message_from_bytes(raw_bytes)
    message_id = (msg.get("Message-ID") or fallback_id).strip()
    references = (msg.get("References") or "").split()
    in_reply_to = msg.get("In-Reply-To")
    if in_reply_to and in_reply_to.strip() not in references:
        references = [in_reply_to.strip(), *references]
    return RawEmail(
        id=message_id,
        sender=(msg.get("From") or "unknown").strip(),
        subject=(msg.get("Subject") or "").strip(),
        body=_extract_body(msg),
        references=references,
    )


class ImapReadOnlyConnector(ReadOnlyEmailConnector):
    """Fetches messages from an injected, already-authenticated IMAP client."""

    def __init__(self, client: ImapClient):
        self._client = client

    def fetch_raw(self, mailbox: str = "INBOX", limit: int = 20) -> list[RawEmail]:
        # readonly=True is the load-bearing guarantee: the server will reject any
        # state-changing command on a read-only-selected mailbox.
        self._client.select(mailbox, readonly=True)
        _typ, data = self._client.search(None, "ALL")
        raw_ids = data[0].split() if data and data[0] else []
        message_ids = raw_ids[: max(0, limit)]

        out: list[RawEmail] = []
        for mid in message_ids:
            _typ, msg_data = self._client.fetch(mid, "(RFC822)")
            if not msg_data or not msg_data[0]:
                continue
            raw_bytes = msg_data[0][1]
            fallback = mid.decode() if isinstance(mid, bytes) else str(mid)
            out.append(parse_raw_email(raw_bytes, fallback_id=fallback))
        return out
