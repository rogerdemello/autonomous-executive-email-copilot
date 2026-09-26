"""Escalations are handed to a colleague, not to the person who wrote in.

A proposed escalation names a *role* — ``legal_team`` — because that is the
judgement the policy can make about a piece of mail. Turning a role into a
mailbox is the workspace's job, and until it has been done an approved
escalation must fail loudly rather than address itself to the outside party.

No network: the provider is the in-memory ``FakeProvider``.
"""

from __future__ import annotations

import re
import uuid

import pytest
from fastapi.testclient import TestClient

from app.copilot.providers.fake import FakeProvider
from app.core.models import ESCALATION_ROLES
from app.main import app
from app.saas import processing_routes, provider_factory
from app.saas.repository import (
    EscalationContactRepository,
    MailboxRepository,
    ProposedActionRepository,
)
from app.saas.sync_service import InboxSyncService

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def csrf_from(html: str) -> str:
    match = CSRF_RE.search(html)
    assert match, "every page with a form must embed a CSRF token"
    return match.group(1)


@pytest.fixture
def client():
    return TestClient(app, follow_redirects=False)


def _signup(client) -> dict:
    resp = client.post(
        "/auth/signup",
        json={
            "email": f"owner_{uuid.uuid4().hex[:12]}@acme.example",
            "password": "a-strong-password",
            "full_name": "Alex Chen",
            "org_name": "Acme",
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _seed_connection(org_id: str, user_id: str) -> str:
    conn = MailboxRepository().upsert_connection(
        org_id=org_id,
        provider="fake",
        account_email="exec@acme.example",
        connected_by=user_id,
        access_token_enc=None,
        refresh_token_enc=None,
        token_expires_at=None,
        scopes=None,
    )
    return conn["id"]


@pytest.fixture
def synced(client, monkeypatch):
    """A workspace with a synced fake mailbox. Returns (data, token, provider).

    Both provider seams are patched to the *same* instance: the API routes build
    one, and ``retry_failed_sends`` builds its own from
    ``provider_factory``. Patching only the first makes a retry write to a
    FakeProvider nobody is holding, and the assertions silently see nothing.
    """
    provider = FakeProvider()
    monkeypatch.setattr(processing_routes, "build_provider", lambda conn: provider)
    monkeypatch.setattr(provider_factory, "build_provider", lambda conn: provider)
    data = _signup(client)
    token = data["access_token"]
    conn_id = _seed_connection(data["organization"]["id"], data["user"]["id"])
    resp = client.post("/inbox/sync", headers=_hdr(token), json={"connection_id": conn_id})
    assert resp.status_code == 200, resp.text
    return data, token, provider


def _pending_escalation(client, token: str) -> dict:
    actions = client.get("/inbox/actions?status=proposed", headers=_hdr(token)).json()["actions"]
    return next(a for a in actions if a["action_type"] == "escalate")


class TestApprovingWithoutAContact:
    def test_approval_fails_and_says_why(self, synced, client):
        """The bug this replaces silently drafted a reply to the sender."""
        data, token, provider = synced
        escalation = _pending_escalation(client, token)

        resp = client.post(f"/inbox/actions/{escalation['id']}/approve", headers=_hdr(token))
        assert resp.status_code == 200, resp.text
        action = resp.json()["action"]

        assert action["status"] == "failed"
        assert "legal team" in (action["last_error"] or "")
        assert "Settings" in (action["last_error"] or "")
        # Nothing was written to the mailbox at all.
        assert provider.drafts == []
        assert provider.sent == []

    def test_the_approval_is_not_thrown_away(self, synced, client):
        """Configure the contact afterwards and the retry path finishes the job.

        The human already decided; making them decide again because an admin had
        not filled in a form is how a queue stops being trusted.
        """
        data, token, provider = synced
        org_id = data["organization"]["id"]
        escalation = _pending_escalation(client, token)
        client.post(f"/inbox/actions/{escalation['id']}/approve", headers=_hdr(token))

        EscalationContactRepository().set_email(org_id, "legal_team", "counsel@acme.example")
        summary = InboxSyncService().retry_failed_sends(org_id=org_id)

        assert summary == {"attempted": 1, "recovered": 1, "still_failing": 0}
        assert len(provider.drafts) == 1
        assert provider.drafts[0]["to"] == "counsel@acme.example"
        assert "Escalating to legal team." in provider.drafts[0]["body"]

        row = ProposedActionRepository().get(org_id, escalation["id"])
        assert row["status"] == "executed"


class TestApprovingWithAContact:
    def test_the_draft_is_addressed_to_the_configured_mailbox(self, synced, client):
        data, token, provider = synced
        EscalationContactRepository().set_email(
            data["organization"]["id"], "legal_team", "counsel@acme.example"
        )
        escalation = _pending_escalation(client, token)

        resp = client.post(f"/inbox/actions/{escalation['id']}/approve", headers=_hdr(token))
        assert resp.status_code == 200, resp.text
        assert resp.json()["action"]["status"] == "executed"

        assert len(provider.drafts) == 1
        assert provider.drafts[0]["to"] == "counsel@acme.example"

    def test_another_workspace_contact_is_not_borrowed(self, synced, client, monkeypatch):
        """The contact is per workspace, like everything else in this schema."""
        data, token, provider = synced
        other = _signup(client)
        EscalationContactRepository().set_email(
            other["organization"]["id"], "legal_team", "counsel@other.example"
        )

        escalation = _pending_escalation(client, token)
        resp = client.post(f"/inbox/actions/{escalation['id']}/approve", headers=_hdr(token))

        assert resp.json()["action"]["status"] == "failed"
        assert provider.drafts == []


class TestTheRepository:
    def test_set_overwrites_rather_than_duplicating(self):
        org_id = f"org-{uuid.uuid4().hex[:12]}"
        repo = EscalationContactRepository()
        repo.set_email(org_id, "legal_team", "first@acme.example")
        repo.set_email(org_id, "legal_team", "second@acme.example")

        assert repo.email_for(org_id, "legal_team") == "second@acme.example"
        assert repo.map_for_org(org_id) == {"legal_team": "second@acme.example"}

    def test_clear_removes_it(self):
        org_id = f"org-{uuid.uuid4().hex[:12]}"
        repo = EscalationContactRepository()
        repo.set_email(org_id, "chief_of_staff", "cos@acme.example")

        assert repo.clear(org_id, "chief_of_staff") is True
        assert repo.email_for(org_id, "chief_of_staff") is None
        assert repo.clear(org_id, "chief_of_staff") is False


class TestTheSettingsForm:
    @pytest.fixture
    def web(self, client):
        email = f"owner_{uuid.uuid4().hex[:10]}@acme.example"
        page = client.get("/signup").text
        resp = client.post(
            "/signup",
            data={
                "csrf_token": csrf_from(page),
                "org_name": "Acme",
                "full_name": "Alex Chen",
                "email": email,
                "password": "a-strong-password",
            },
        )
        assert resp.status_code == 303
        return client

    def test_every_role_is_listed_and_unset_is_visible(self, web):
        page = web.get("/app/settings").text
        for role in ESCALATION_ROLES:
            assert role.replace("_", " ") in page
        assert "not set" in page

    def test_saving_one_and_then_clearing_it(self, web):
        token = csrf_from(web.get("/app/settings").text)
        resp = web.post(
            "/app/settings/escalation",
            data={"csrf_token": token, "role": "legal_team", "email": "counsel@acme.example"},
        )
        assert resp.status_code == 303
        assert "notice=escalation_set" in resp.headers["location"]
        assert "counsel@acme.example" in web.get("/app/settings").text

        resp = web.post(
            "/app/settings/escalation",
            data={"csrf_token": token, "role": "legal_team", "email": "   "},
        )
        assert resp.status_code == 303
        assert "notice=escalation_cleared" in resp.headers["location"]
        assert "counsel@acme.example" not in web.get("/app/settings").text

    def test_a_malformed_address_is_refused(self, web):
        token = csrf_from(web.get("/app/settings").text)
        resp = web.post(
            "/app/settings/escalation",
            data={"csrf_token": token, "role": "legal_team", "email": "not an address"},
        )
        assert resp.status_code == 400
        assert "does not look like an email address" in resp.text

    def test_an_unknown_role_is_refused(self, web):
        """The form posts a hidden field; nothing stops a crafted post."""
        token = csrf_from(web.get("/app/settings").text)
        resp = web.post(
            "/app/settings/escalation",
            data={"csrf_token": token, "role": "board_of_directors", "email": "b@acme.example"},
        )
        assert resp.status_code == 400
        assert "not an escalation role" in resp.text

    def test_no_csrf_token_no_write(self, web):
        resp = web.post(
            "/app/settings/escalation",
            data={"role": "legal_team", "email": "counsel@acme.example"},
        )
        assert resp.status_code == 403


class TestTheApprovalsPage:
    """The reviewer is told before the click, not by a failed send afterwards."""

    @pytest.fixture
    def with_demo_mailbox(self, client):
        page = client.get("/signup").text
        resp = client.post(
            "/signup",
            data={
                "csrf_token": csrf_from(page),
                "org_name": "Acme",
                "full_name": "Alex Chen",
                "email": f"owner_{uuid.uuid4().hex[:10]}@acme.example",
                "password": "a-strong-password",
            },
        )
        assert resp.status_code == 303
        connect = client.get("/app/connect").text
        resp = client.post("/app/connect/demo", data={"csrf_token": csrf_from(connect)})
        assert resp.status_code == 303
        return client

    def test_it_warns_when_no_mailbox_is_set(self, with_demo_mailbox):
        page = with_demo_mailbox.get("/app/approvals").text
        # The demo mailbox raises legal escalations; if it ever stops, this
        # assertion is the thing that should fail, not a silent skip.
        assert "→ legal team" in page
        assert "No mailbox is set for escalations to" in page
        assert "no mailbox set" in page

    def test_it_names_the_recipient_once_one_is_set(self, with_demo_mailbox, client):
        token = csrf_from(with_demo_mailbox.get("/app/settings").text)
        for role in ESCALATION_ROLES:
            with_demo_mailbox.post(
                "/app/settings/escalation",
                data={"csrf_token": token, "role": role, "email": f"{role}@acme.example"},
            )

        page = with_demo_mailbox.get("/app/approvals").text
        assert "No mailbox is set for escalations to" not in page
        assert "legal_team@acme.example" in page
        assert "with the original attached" in page
