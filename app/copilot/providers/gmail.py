"""Gmail provider (Google API).

Reads and acts on a real Gmail mailbox using an OAuth access token. All network
I/O goes through a single injectable ``transport`` callable so the whole provider
is testable with no real httpx and no network — the default transport is the only
place httpx is touched. On a 401 the provider refreshes its token once (via the
injected ``token_refresher``) and retries.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from urllib.parse import quote

from .base import FetchedMessage, MailProvider, WriteResult, write_guard
from .html_text import html_to_text, looks_like_html

_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


def _seg(value: str) -> str:
    """Quote a value for use as a URL path segment or query value.

    Message and folder ids come back from the provider API and round-trip
    through our database; quoting keeps a crafted id from rewriting the
    request path or smuggling extra query parameters.
    """
    return quote(str(value), safe="")


# transport(method, url, token, json_body) -> (status_code, response_json)
Transport = Callable[[str, str, str, dict | None], tuple[int, dict]]


class ProviderError(Exception):
    """A non-auth error from the mail provider API."""


def _httpx_transport(method: str, url: str, token: str, json_body: dict | None) -> tuple[int, dict]:
    import httpx

    headers = {"Authorization": f"Bearer {token}"}
    resp = httpx.request(method, url, headers=headers, json=json_body, timeout=20.0)
    try:
        data = resp.json()
    except (ValueError, json.JSONDecodeError):
        data = {}
    return resp.status_code, data


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_b64url(data: str) -> str:
    if not data:
        return ""
    data += "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(data).decode("utf-8", "replace")
    except (ValueError, base64.binascii.Error):  # type: ignore[attr-defined]
        return ""


def _internal_date_to_iso(internal_date: str) -> str:
    """Gmail's ``internalDate`` (epoch milliseconds, as a string) → ISO-8601 UTC.

    Everything downstream — the inbox's ordering column, ``_short_time`` in the
    web UI, mixed-provider sorting against Graph's ISO timestamps — assumes ISO.
    Stored raw, a real Gmail inbox renders '1723800000000' as the arrival time.
    """
    try:
        millis = int(internal_date)
    except (TypeError, ValueError):
        return internal_date or ""
    from datetime import datetime, timezone

    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc).isoformat()


def _header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _find_part(payload: dict, mime_type: str) -> str:
    """First decoded part of ``mime_type`` anywhere in the MIME tree."""
    if payload.get("mimeType") == mime_type:
        decoded = _decode_b64url(payload.get("body", {}).get("data", ""))
        if decoded:
            return decoded
    for part in payload.get("parts", []) or []:
        found = _find_part(part, mime_type)
        if found:
            return found
    return ""


def _extract_body(payload: dict) -> str:
    """The readable text of a Gmail message.

    ``text/plain`` wins when the sender provided it: it is what they wrote,
    already wrapped the way they wrote it. Failing that the ``text/html``
    alternative is rendered to text — a large share of real mail is HTML-only
    (notifications, newsletters, anything from a modern client), and this used
    to fall through to the raw-body fallback below and store the markup. That
    markup then *was* the email: shown in the reader, handed to the drafter as
    the message, and checked by the verifier as the source.
    """
    plain = _find_part(payload, "text/plain")
    if plain:
        return plain

    html = _find_part(payload, "text/html")
    if html:
        return html_to_text(html)

    # Fallback: a top-level body with data and no declared part structure.
    raw = _decode_b64url(payload.get("body", {}).get("data", ""))
    return html_to_text(raw) if looks_like_html(raw) else raw


class GmailProvider(MailProvider):
    def __init__(
        self,
        access_token: str,
        *,
        token_refresher: Callable[[], str] | None = None,
        transport: Transport | None = None,
    ) -> None:
        self._token = access_token
        self._refresh = token_refresher
        self._transport = transport or _httpx_transport

    def _call(self, method: str, url: str, json_body: dict | None = None) -> dict:
        status, data = self._transport(method, url, self._token, json_body)
        if status == 401 and self._refresh is not None:
            self._token = self._refresh()
            status, data = self._transport(method, url, self._token, json_body)
        if status >= 400:
            raise ProviderError(f"Gmail API {method} {url} failed: {status} {data}")
        return data

    # -- read ---------------------------------------------------------------
    def fetch_messages(self, folder: str = "INBOX", limit: int = 25) -> list[FetchedMessage]:
        listing = self._call(
            "GET", f"{_BASE}/messages?maxResults={int(limit)}&labelIds={_seg(folder)}"
        )
        out: list[FetchedMessage] = []
        for ref in listing.get("messages", []) or []:
            msg = self._call("GET", f"{_BASE}/messages/{_seg(ref['id'])}?format=full")
            out.append(self._to_fetched(msg))
        return out

    def _to_fetched(self, msg: dict) -> FetchedMessage:
        payload = msg.get("payload", {})
        headers = payload.get("headers", [])
        from_raw = _header(headers, "From")
        sender, sender_name = self._split_from(from_raw)
        references = [r for r in _header(headers, "References").split() if r]
        return FetchedMessage(
            provider_message_id=msg.get("id", ""),
            thread_id=msg.get("threadId", ""),
            sender=sender,
            sender_name=sender_name,
            subject=_header(headers, "Subject"),
            body=_extract_body(payload) or msg.get("snippet", ""),
            references=references,
            received_at=_internal_date_to_iso(msg.get("internalDate", "")),
        )

    @staticmethod
    def _split_from(from_header: str) -> tuple[str, str]:
        # "Alex Vance <alex@acme.com>" -> ("alex@acme.com", "Alex Vance")
        if "<" in from_header and ">" in from_header:
            name = from_header.split("<", 1)[0].strip().strip('"')
            addr = from_header.split("<", 1)[1].split(">", 1)[0].strip()
            return addr, name
        return from_header.strip(), ""

    # -- write --------------------------------------------------------------
    def _raw_message(
        self, to: str, subject: str, body: str, *, in_reply_to: str = "", references: str = ""
    ) -> str:
        # Built with EmailMessage rather than hand-concatenated header lines:
        # a non-ASCII subject ("Re: Présentation") in a raw f-string is an
        # RFC-violating header, and whether it survives depends on the
        # receiving server. EmailMessage encodes headers per RFC 2047.
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["To"] = to
        msg["Subject"] = subject
        # Gmail's send API documents In-Reply-To/References as required for
        # threadId to apply — and the *recipient's* client threads only by
        # these headers, threadId being a Gmail-internal concept. Without
        # them, every approved reply lands at the other end as a brand-new
        # conversation.
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = f"{references} {in_reply_to}".strip()
        msg.set_content(body)
        return _b64url(msg.as_bytes())

    def _reply_context(self, provider_message_id: str) -> tuple[str, str, str, str, str]:
        """(to, subject, thread_id, message_id_header, references) of a message.

        ``to`` honours Reply-To when the sender set one — ticket systems and
        no-reply senders route replies away from From, and answering From
        anyway sends the approved reply somewhere nobody reads.
        """
        msg = self._call("GET", f"{_BASE}/messages/{_seg(provider_message_id)}?format=metadata")
        headers = msg.get("payload", {}).get("headers", [])
        to, _ = self._split_from(_header(headers, "Reply-To") or _header(headers, "From"))
        return (
            to,
            _header(headers, "Subject"),
            msg.get("threadId", ""),
            _header(headers, "Message-ID"),
            _header(headers, "References"),
        )

    @write_guard
    def send_reply(self, provider_message_id: str, body: str) -> WriteResult:
        to, subject, thread_id, message_id, references = self._reply_context(provider_message_id)
        reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        raw = self._raw_message(
            to, reply_subject, body, in_reply_to=message_id, references=references
        )
        data = self._call("POST", f"{_BASE}/messages/send", {"raw": raw, "threadId": thread_id})
        return WriteResult(ok=True, provider_ref=data.get("id"))

    @write_guard
    def create_escalation_draft(
        self, provider_message_id: str, body: str, *, to: str
    ) -> WriteResult:
        """Draft a forward of this message to ``to``.

        Gmail has no forward endpoint, so the forward is built: a new draft
        addressed to the internal recipient, with the original quoted beneath
        the hand-off note, and deliberately **no** ``threadId``. Threading it
        would put an internal hand-off inside the customer's own conversation,
        one careless Send away from telling them they are being escalated.
        """
        if not to.strip():
            # Unreachable via the sync service, which resolves the address
            # first; a direct caller gets a WriteResult, not a draft addressed
            # to nobody, which Gmail would happily accept.
            return WriteResult(ok=False, detail="no escalation recipient was given")
        # One ``format=full`` read rather than ``_reply_context``'s metadata
        # fetch plus a second one for the body: a forward needs both, and the
        # full response already carries the headers.
        msg = self._call("GET", f"{_BASE}/messages/{_seg(provider_message_id)}?format=full")
        payload = msg.get("payload", {})
        headers = payload.get("headers", [])
        sender, _sender_name = self._split_from(_header(headers, "From"))
        subject = _header(headers, "Subject")
        forward_subject = subject if subject.lower().startswith("fwd:") else f"Fwd: {subject}"
        raw = self._raw_message(
            to,
            forward_subject,
            f"{body}\n\n{self._quoted(payload, msg, sender, subject)}".strip(),
        )
        data = self._call("POST", f"{_BASE}/drafts", {"message": {"raw": raw}})
        return WriteResult(ok=True, provider_ref=data.get("id"))

    @staticmethod
    def _quoted(payload: dict, msg: dict, sender: str, subject: str) -> str:
        """The original, quoted, so the hand-off carries its own context.

        A forward whose body is only "Escalating to legal_team" asks the reader
        to go and find the mail themselves — which is most of the work being
        handed over.
        """
        original = _extract_body(payload) or msg.get("snippet", "")
        quoted = "\n".join(f"> {line}" for line in original.splitlines())
        return (
            f"---------- Forwarded message ----------\n"
            f"From: {sender}\n"
            f"Subject: {subject}\n\n"
            f"{quoted}"
        )

    @write_guard
    def add_label(self, provider_message_id: str, label: str) -> WriteResult:
        label_id = self._ensure_label(label)
        data = self._call(
            "POST",
            f"{_BASE}/messages/{_seg(provider_message_id)}/modify",
            {"addLabelIds": [label_id]},
        )
        return WriteResult(ok=True, provider_ref=data.get("id"))

    @write_guard
    def archive(self, provider_message_id: str) -> WriteResult:
        data = self._call(
            "POST",
            f"{_BASE}/messages/{_seg(provider_message_id)}/modify",
            {"removeLabelIds": ["INBOX"]},
        )
        return WriteResult(ok=True, provider_ref=data.get("id"))

    @write_guard
    def add_labels_batch(self, provider_message_ids: list[str], label: str) -> WriteResult:
        """Apply one label to many messages in a single API call.

        Gmail's ``batchModify`` takes up to 1000 ids per request, so one label
        lookup plus one write replaces the 2N sequential calls the per-message
        path costs — the difference between a 100-message first sync being ~200
        HTTP round-trips and being 2. The sync service uses this when present.
        """
        if not provider_message_ids:
            return WriteResult(ok=True)
        label_id = self._ensure_label(label)
        for start in range(0, len(provider_message_ids), 1000):
            chunk = provider_message_ids[start : start + 1000]
            self._call(
                "POST",
                f"{_BASE}/messages/batchModify",
                {"ids": [str(i) for i in chunk], "addLabelIds": [label_id]},
            )
        return WriteResult(ok=True)

    def _ensure_label(self, name: str) -> str:
        """Return the id of the Gmail label ``name``, creating it if missing."""
        listing = self._call("GET", f"{_BASE}/labels")
        for lab in listing.get("labels", []) or []:
            if lab.get("name") == name:
                return lab.get("id", name)
        created = self._call("POST", f"{_BASE}/labels", {"name": name})
        return created.get("id", name)
