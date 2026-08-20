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


def _extract_body(payload: dict) -> str:
    """Walk the MIME tree for the first text/plain part."""
    if payload.get("mimeType") == "text/plain":
        return _decode_b64url(payload.get("body", {}).get("data", ""))
    for part in payload.get("parts", []) or []:
        text = _extract_body(part)
        if text:
            return text
    # Fallback: a top-level body with data.
    return _decode_b64url(payload.get("body", {}).get("data", ""))


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
    def _raw_message(self, to: str, subject: str, body: str) -> str:
        mime = f"To: {to}\r\nSubject: {subject}\r\nContent-Type: text/plain; charset=UTF-8\r\n\r\n{body}"
        return _b64url(mime.encode("utf-8"))

    def _recipient_and_thread(self, provider_message_id: str) -> tuple[str, str, str]:
        msg = self._call("GET", f"{_BASE}/messages/{_seg(provider_message_id)}?format=metadata")
        headers = msg.get("payload", {}).get("headers", [])
        sender, _ = self._split_from(_header(headers, "From"))
        subject = _header(headers, "Subject")
        return sender, subject, msg.get("threadId", "")

    @write_guard
    def send_reply(self, provider_message_id: str, body: str) -> WriteResult:
        to, subject, thread_id = self._recipient_and_thread(provider_message_id)
        reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        payload = {"raw": self._raw_message(to, reply_subject, body), "threadId": thread_id}
        data = self._call("POST", f"{_BASE}/messages/send", payload)
        return WriteResult(ok=True, provider_ref=data.get("id"))

    @write_guard
    def create_draft(self, provider_message_id: str, body: str) -> WriteResult:
        to, subject, thread_id = self._recipient_and_thread(provider_message_id)
        payload = {"message": {"raw": self._raw_message(to, subject, body), "threadId": thread_id}}
        data = self._call("POST", f"{_BASE}/drafts", payload)
        return WriteResult(ok=True, provider_ref=data.get("id"))

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
