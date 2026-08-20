"""Microsoft 365 provider (Microsoft Graph).

Same shape as the Gmail provider: all network I/O goes through an injectable
``transport`` so it's testable with no httpx and no network; a 401 refreshes the
token once and retries. Graph returns full message bodies in the list response,
so reads need no per-message fetch.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from .base import FetchedMessage, MailProvider, WriteResult, write_guard
from .gmail import ProviderError, Transport, _seg

_BASE = "https://graph.microsoft.com/v1.0/me"


def _httpx_transport(method: str, url: str, token: str, json_body: dict | None) -> tuple[int, dict]:
    import httpx

    headers = {"Authorization": f"Bearer {token}"}
    resp = httpx.request(method, url, headers=headers, json=json_body, timeout=20.0)
    try:
        data = resp.json()
    except (ValueError, json.JSONDecodeError):
        data = {}
    return resp.status_code, data


class MicrosoftGraphProvider(MailProvider):
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
            raise ProviderError(f"Graph API {method} {url} failed: {status} {data}")
        return data

    def fetch_messages(self, folder: str = "INBOX", limit: int = 25) -> list[FetchedMessage]:
        mailfolder = "inbox" if folder.upper() == "INBOX" else folder
        data = self._call(
            "GET", f"{_BASE}/mailFolders/{_seg(mailfolder)}/messages?$top={int(limit)}"
        )
        return [self._to_fetched(m) for m in data.get("value", []) or []]

    def _to_fetched(self, m: dict) -> FetchedMessage:
        sender_obj = (m.get("from") or {}).get("emailAddress", {})
        body = m.get("body", {}) or {}
        return FetchedMessage(
            provider_message_id=m.get("id", ""),
            thread_id=m.get("conversationId", ""),
            sender=sender_obj.get("address", ""),
            sender_name=sender_obj.get("name", ""),
            subject=m.get("subject", ""),
            body=body.get("content") or m.get("bodyPreview", ""),
            references=[],
            received_at=m.get("receivedDateTime", ""),
        )

    @write_guard
    def send_reply(self, provider_message_id: str, body: str) -> WriteResult:
        data = self._call(
            "POST", f"{_BASE}/messages/{_seg(provider_message_id)}/reply", {"comment": body}
        )
        return WriteResult(ok=True, provider_ref=data.get("id"))

    @write_guard
    def create_draft(self, provider_message_id: str, body: str) -> WriteResult:
        data = self._call(
            "POST", f"{_BASE}/messages/{_seg(provider_message_id)}/createReply", {"comment": body}
        )
        return WriteResult(ok=True, provider_ref=data.get("id"))

    @write_guard
    def add_label(self, provider_message_id: str, label: str) -> WriteResult:
        # Graph has no labels; the nearest concept is a category.
        data = self._call(
            "PATCH", f"{_BASE}/messages/{_seg(provider_message_id)}", {"categories": [label]}
        )
        return WriteResult(ok=True, provider_ref=data.get("id"))

    @write_guard
    def archive(self, provider_message_id: str) -> WriteResult:
        data = self._call(
            "POST",
            f"{_BASE}/messages/{_seg(provider_message_id)}/move",
            {"destinationId": "archive"},
        )
        return WriteResult(ok=True, provider_ref=data.get("id"))
