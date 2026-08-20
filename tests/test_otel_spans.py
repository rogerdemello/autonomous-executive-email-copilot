"""OTEL spans over the value loop (sync → draft → approve).

`in_span` is a no-op without OpenTelemetry installed, so these tests assert
the *instrumentation points* exist — the span names the trace will carry —
rather than exercising a real exporter.
"""

from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.copilot.providers.fake import FakeProvider
from app.main import app
from app.saas.repository import MailboxRepository
from app.saas.sync_service import InboxSyncService


def _org(client) -> dict:
    resp = client.post(
        "/auth/signup",
        json={
            "email": f"span_{uuid.uuid4().hex[:10]}@example.com",
            "password": "hunter2pass",
            "full_name": "S",
            "org_name": "Span Org",
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_sync_and_approve_open_spans(monkeypatch):
    recorded: list[str] = []
    from app.saas import sync_service

    def spy(name, attributes=None, kind=None):
        recorded.append(name)
        return nullcontext()

    monkeypatch.setattr(sync_service, "in_span", spy)

    client = TestClient(app)
    org = _org(client)
    conn = MailboxRepository().upsert_connection(
        org_id=org["organization"]["id"],
        provider="imap-dev",
        account_email=f"exec-{uuid.uuid4().hex[:8]}@span.example",
        connected_by=org["user"]["id"],
        access_token_enc=None,
        refresh_token_enc=None,
        token_expires_at=None,
        scopes=None,
    )
    service = InboxSyncService()
    service.sync(
        org_id=conn["org_id"],
        user_id=org["user"]["id"],
        connection_id=conn["id"],
        provider=FakeProvider(),
    )
    assert "inbox.sync" in recorded

    resp = client.get(
        "/inbox/actions?status=proposed",
        headers={"Authorization": f"Bearer {org['access_token']}"},
    )
    action = next(a for a in resp.json()["actions"] if a["action_type"] == "reply")
    service.approve(
        org_id=conn["org_id"],
        user_id=org["user"]["id"],
        action_id=action["id"],
        provider=FakeProvider(),
    )
    assert "inbox.approve" in recorded


def test_drafting_opens_a_span(monkeypatch):
    recorded: list[str] = []
    import telemetry.otel as otel

    def spy(name, attributes=None, kind=None):
        recorded.append(name)
        return nullcontext()

    monkeypatch.setattr(otel, "in_span", spy)

    from app.copilot.providers.base import FetchedMessage
    from app.llm.drafter import EmailDrafter

    stub = SimpleNamespace(
        generate=lambda messages, **kw: SimpleNamespace(
            content=json.dumps({"body": "ok body for the span test.", "confidence": 0.5}),
            model="stub",
            usage=None,
        )
    )
    EmailDrafter(provider=stub).draft(
        message=FetchedMessage(
            provider_message_id="m-span",
            thread_id="t",
            sender="a@b.c",
            sender_name="",
            subject="s",
            body="please confirm",
        ),
        action_type="reply",
    )
    assert "llm.draft" in recorded
