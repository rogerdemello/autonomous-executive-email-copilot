"""Microsoft Graph provider tests. No network — transport is injected."""

from __future__ import annotations

from app.copilot.providers.graph import MicrosoftGraphProvider


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


def test_fetch_asks_for_the_newest_messages_first():
    """Triage is about what just arrived.

    A mailbox is capped at ``inbox_sync_limit`` per sweep, so if the service
    ever returned oldest-first a real account would sync mail from years ago,
    every time, and never surface the message someone is waiting on. Gmail's
    list endpoint documents reverse-chronological order; Graph's does not, so
    this asks explicitly rather than trusting a default.
    """
    seen = []

    def transport(method, url, token, json_body):
        seen.append(url)
        return 200, {"value": []}

    MicrosoftGraphProvider("tok", transport=transport).fetch_messages(limit=50)

    assert len(seen) == 1
    assert "$top=50" in seen[0]
    assert "$orderby=receivedDateTime%20desc" in seen[0]


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
    transport = RecordingTransport(
        {
            ("GET", "/messages/m1"): (200, {"id": "m1", "categories": []}),
            ("PATCH", "/messages/m1"): (200, {"id": "m1"}),
        }
    )
    provider = MicrosoftGraphProvider("tok", transport=transport)
    result = provider.add_label("m1", "urgent")
    assert result.ok
    method, url, _token, body = transport.calls[-1]
    assert method == "PATCH"
    assert body == {"categories": ["urgent"]}


def test_add_label_keeps_the_categories_already_on_the_message():
    """``categories`` is a collection a PATCH replaces, not appends to.

    Sending only our own category deleted every category the person had filed
    the message under — in their mailbox, as a side effect of us triaging it.
    """
    transport = RecordingTransport(
        {
            ("GET", "/messages/m1"): (200, {"id": "m1", "categories": ["Blue category", "Q3"]}),
            ("PATCH", "/messages/m1"): (200, {"id": "m1"}),
        }
    )
    provider = MicrosoftGraphProvider("tok", transport=transport)
    assert provider.add_label("m1", "deferred").ok

    method, _url, _token, body = transport.calls[-1]
    assert method == "PATCH"
    assert body == {"categories": ["Blue category", "Q3", "deferred"]}


def test_add_label_does_not_write_when_the_category_is_already_there():
    """Re-syncing a mailbox must not rewrite messages it has already filed."""
    transport = RecordingTransport(
        {("GET", "/messages/m1"): (200, {"id": "m1", "categories": ["Deferred"]})}
    )
    provider = MicrosoftGraphProvider("tok", transport=transport)
    result = provider.add_label("m1", "deferred")

    assert result.ok
    assert result.provider_ref == "m1"
    assert [m for m, _u, _t, _b in transport.calls] == ["GET"]


def test_two_labels_on_one_message_both_survive():
    """The failure this actually caused, against a mailbox that remembers.

    A message can be downgraded to ``deferred`` and classified in the same
    sweep. With a replacing PATCH, only whichever write landed second existed
    afterwards — so our own labels did not accumulate either.
    """
    mailbox = {"id": "m1", "categories": []}

    def transport(method, url, token, json_body):
        if method == "GET":
            return 200, dict(mailbox)
        mailbox["categories"] = list(json_body["categories"])
        return 200, {"id": "m1"}

    provider = MicrosoftGraphProvider("tok", transport=transport)
    assert provider.add_label("m1", "deferred").ok
    assert provider.add_label("m1", "contract").ok

    assert mailbox["categories"] == ["deferred", "contract"]


def test_escalation_draft_forwards_to_the_colleague():
    """``createForward``, not ``createReply``.

    createReply addressed the hand-off to the outside party who wrote in.
    createForward is this operation, and Graph attaches the original itself.
    """
    transport = RecordingTransport({("POST", "/messages/m1/createForward"): (201, {"id": "d1"})})
    provider = MicrosoftGraphProvider("tok", transport=transport)
    result = provider.create_escalation_draft("m1", "Escalating.", to="counsel@acme.example")
    assert result.ok
    assert result.provider_ref == "d1"

    method, url, _token, body = transport.calls[-1]
    assert method == "POST"
    assert "/messages/m1/createForward" in url
    assert body["comment"] == "Escalating."
    assert body["toRecipients"] == [{"emailAddress": {"address": "counsel@acme.example"}}]


def test_escalation_draft_without_a_recipient_writes_nothing():
    transport = RecordingTransport({})
    provider = MicrosoftGraphProvider("tok", transport=transport)
    result = provider.create_escalation_draft("m1", "Escalating.", to="")
    assert result.ok is False
    assert transport.calls == []


def test_archive_moves_message():
    transport = RecordingTransport({("POST", "/messages/m1/move"): (201, {"id": "m1"})})
    provider = MicrosoftGraphProvider("tok", transport=transport)
    result = provider.archive("m1")
    assert result.ok
    method, url, _token, body = transport.calls[0]
    assert body == {"destinationId": "archive"}


def test_message_ids_are_url_quoted():
    """A crafted provider_message_id must not rewrite the request path —
    ids are percent-encoded into URLs."""
    transport = RecordingTransport({})
    provider = MicrosoftGraphProvider("tok", transport=transport)
    provider.send_reply("m1/../../users/other", "hi")
    _, url, _token, _body = transport.calls[0]
    assert "/messages/m1%2F..%2F..%2Fusers%2Fother/reply" in url


def test_write_failure_returns_not_ok():
    # Bug 1b: a write API error becomes WriteResult(ok=False), not an exception.
    transport = RecordingTransport({("POST", "/messages/m1/reply"): (503, {"error": "down"})})
    provider = MicrosoftGraphProvider("tok", transport=transport)
    result = provider.send_reply("m1", "hi")
    assert result.ok is False
    assert result.detail
