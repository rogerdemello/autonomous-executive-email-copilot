"""Microsoft Graph provider tests. No network — transport is injected."""

from __future__ import annotations

from env.product.providers.graph import MicrosoftGraphProvider


class RecordingTransport:
    def __init__(self, responses):
        self._responses = responses
        self.calls = []

    def __call__(self, method, url, token, json_body):
        self.calls.append((method, url, token, json_body))
        for (m, needle), resp in self._responses.items():
            if m == method and needle in url:
                return resp
        return 200, {}


def _message(mid: str) -> dict:
    return {
        "id": mid,
        "conversationId": f"c-{mid}",
        "subject": "Contract review",
        "bodyPreview": "preview",
        "from": {"emailAddress": {"address": "legal@vendor.example", "name": "Legal"}},
        "body": {"contentType": "text", "content": "Please review the attached contract."},
        "receivedDateTime": "2026-07-21T08:00:00Z",
    }


def test_fetch_maps_messages():
    transport = RecordingTransport(
        {("GET", "/mailFolders/inbox/messages"): (200, {"value": [_message("m1")]})}
    )
    provider = MicrosoftGraphProvider("tok", transport=transport)
    msgs = provider.fetch_messages()
    assert len(msgs) == 1
    assert msgs[0].provider_message_id == "m1"
    assert msgs[0].thread_id == "c-m1"
    assert msgs[0].sender == "legal@vendor.example"
    assert msgs[0].subject == "Contract review"
    assert "review the attached contract" in msgs[0].body


def test_401_triggers_single_refresh_and_retry():
    state = {"n": 0}

    def transport(method, url, token, json_body):
        state["n"] += 1
        if state["n"] == 1:
            return 401, {"error": {"code": "InvalidAuthenticationToken"}}
        return 200, {"value": []}

    refreshed = {"count": 0}

    def refresher():
        refreshed["count"] += 1
        return "fresh"

    provider = MicrosoftGraphProvider("stale", token_refresher=refresher, transport=transport)
    provider.fetch_messages()
    assert refreshed["count"] == 1
    assert provider._token == "fresh"


def test_send_reply_hits_reply_endpoint():
    transport = RecordingTransport({("POST", "/reply"): (202, {})})
    provider = MicrosoftGraphProvider("tok", transport=transport)
    result = provider.send_reply("m1", "Acknowledged.")
    assert result.ok
    assert any(m == "POST" and "/messages/m1/reply" in u for m, u, _, _ in transport.calls)


def test_add_label_uses_categories():
    transport = RecordingTransport({("PATCH", "/messages/m1"): (200, {"id": "m1"})})
    provider = MicrosoftGraphProvider("tok", transport=transport)
    result = provider.add_label("m1", "urgent")
    assert result.ok
    method, url, _token, body = transport.calls[0]
    assert method == "PATCH"
    assert body == {"categories": ["urgent"]}


def test_create_draft_hits_create_reply():
    transport = RecordingTransport({("POST", "/messages/m1/createReply"): (201, {"id": "d1"})})
    provider = MicrosoftGraphProvider("tok", transport=transport)
    result = provider.create_draft("m1", "Escalating.")
    assert result.ok
    assert result.provider_ref == "d1"
    assert any("/messages/m1/createReply" in u for _, u, _, _ in transport.calls)


def test_archive_moves_message():
    transport = RecordingTransport({("POST", "/messages/m1/move"): (201, {"id": "m1"})})
    provider = MicrosoftGraphProvider("tok", transport=transport)
    result = provider.archive("m1")
    assert result.ok
    method, url, _token, body = transport.calls[0]
    assert body == {"destinationId": "archive"}


def test_write_failure_returns_not_ok():
    # Bug 1b: a write API error becomes WriteResult(ok=False), not an exception.
    transport = RecordingTransport({("POST", "/messages/m1/reply"): (503, {"error": "down"})})
    provider = MicrosoftGraphProvider("tok", transport=transport)
    result = provider.send_reply("m1", "hi")
    assert result.ok is False
    assert result.detail
