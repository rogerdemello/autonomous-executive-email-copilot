"""Tests for org data export and deletion (GDPR data lifecycle, Track B)."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.saas import processing_routes
from app.saas.data_lifecycle import DataLifecycleService
from app.saas.repository import MailboxRepository


@pytest.fixture
def client():
    return TestClient(app)


def _email() -> str:
    return f"user_{uuid.uuid4().hex[:12]}@example.com"


def _signup(client, org_name="Acme"):
    resp = client.post(
        "/auth/signup",
        json={"email": _email(), "password": "hunter2pass", "full_name": "U", "org_name": org_name},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _hdr(token):
    return {"Authorization": f"Bearer {token}"}


class TestExport:
    def test_export_bundle_is_complete_and_secret_free(self, client, monkeypatch):
        from app.copilot.providers.fake import FakeProvider

        monkeypatch.setattr(processing_routes, "build_provider", lambda conn: FakeProvider())
        owner = _signup(client)
        token = owner["access_token"]
        org_id = owner["organization"]["id"]

        # Invite a member and process an inbox so several tables have rows.
        client.post(
            "/org/members",
            headers=_hdr(token),
            json={
                "email": _email(),
                "full_name": "M",
                "role": "member",
                "temp_password": "temppass12",
            },
        )
        conn = MailboxRepository().upsert_connection(
            org_id=org_id,
            provider="fake",
            account_email="exec@acme.example",
            connected_by=owner["user"]["id"],
            access_token_enc=None,
            refresh_token_enc=None,
            token_expires_at=None,
            scopes=None,
        )
        client.post("/inbox/sync", headers=_hdr(token), json={"connection_id": conn["id"]})

        bundle = client.get("/org/export", headers=_hdr(token)).json()
        assert bundle["organization"]["id"] == org_id
        assert len(bundle["users"]) == 2
        assert len(bundle["licenses"]) >= 1
        assert len(bundle["mailbox_connections"]) == 1
        assert len(bundle["processed_messages"]) == 4
        assert bundle["proposed_actions"]
        assert bundle["audit_log"]

        # No secrets anywhere in the export.
        import json

        blob = json.dumps(bundle)
        assert "password_hash" not in blob
        assert "access_token_enc" not in blob
        assert "refresh_token_enc" not in blob

    def test_export_requires_owner(self, client):
        owner = _signup(client)
        member_email = _email()
        client.post(
            "/org/members",
            headers=_hdr(owner["access_token"]),
            json={
                "email": member_email,
                "full_name": "M",
                "role": "member",
                "temp_password": "temppass12",
            },
        )
        member_token = client.post(
            "/auth/login", json={"email": member_email, "password": "temppass12"}
        ).json()["access_token"]
        assert client.get("/org/export", headers=_hdr(member_token)).status_code == 403


def _rows_left_for(org_id: str) -> dict[str, int]:
    """Rows still held for ``org_id`` in *every* org-scoped table.

    Read from the live metadata rather than a hand-kept list: the failure this
    guards against is a table nobody remembered to put on a list.
    """
    return DataLifecycleService().remaining_rows(org_id)


class TestEveryTenantTableIsCovered:
    """Erasure that misses a table is a compliance claim that is not true.

    ``saas_llm_usage`` and ``saas_escalation_contacts`` were both added after
    this service was written, and neither was added to it. The second holds the
    email addresses of the customer's colleagues and has a foreign key to the
    organization — so "delete my workspace" left personal data behind on SQLite
    and raised an IntegrityError on Postgres.
    """

    def test_no_org_scoped_table_is_missing_from_export_and_delete(self):
        from app.saas.data_lifecycle import uncovered_tenant_tables

        assert uncovered_tenant_tables() == [], (
            "A table with an org_id column is not in app.saas.data_lifecycle.TENANT_TABLES. "
            "Add it, or 'erase my workspace' will leave that customer's data behind."
        )

    def test_the_guard_notices_a_table_that_is_not_covered(self):
        """Prove the check is not vacuous: a new org-scoped table must trip it."""
        from sqlalchemy import Column, String, Table

        from app.saas.data_lifecycle import Base, uncovered_tenant_tables

        # `Base` from the module under test, not from `app.core.db`: the
        # migration tests importlib.reload() that module, which creates a *new*
        # declarative base, so after them the two are different objects and a
        # probe registered on the wrong one is invisible to the function.
        probe = Table("saas_probe_not_covered", Base.metadata, Column("org_id", String(32)))
        try:
            assert uncovered_tenant_tables() == ["saas_probe_not_covered"]
        finally:
            Base.metadata.remove(probe)
        assert uncovered_tenant_tables() == []

    def test_delete_leaves_no_row_behind_in_any_table(self, client, monkeypatch):
        from app.copilot.providers.fake import FakeProvider
        from app.saas.repository import EscalationContactRepository, LlmUsageRepository

        monkeypatch.setattr(processing_routes, "build_provider", lambda conn: FakeProvider())
        owner = _signup(client)
        token = owner["access_token"]
        org_id = owner["organization"]["id"]
        conn = MailboxRepository().upsert_connection(
            org_id=org_id,
            provider="fake",
            account_email="exec@acme.example",
            connected_by=owner["user"]["id"],
            access_token_enc=None,
            refresh_token_enc=None,
            token_expires_at=None,
            scopes=None,
        )
        client.post("/inbox/sync", headers=_hdr(token), json={"connection_id": conn["id"]})
        # The two tables the old delete never touched.
        EscalationContactRepository().set_email(org_id, "legal_team", "counsel@acme.example")
        LlmUsageRepository().record(org_id=org_id, cost_usd=0.01, model="m", purpose="draft")
        assert {"saas_escalation_contacts", "saas_llm_usage"} <= set(_rows_left_for(org_id))

        resp = client.request(
            "DELETE",
            "/org",
            headers=_hdr(token),
            json={"confirm": owner["organization"]["slug"]},
        )
        assert resp.status_code == 200, resp.text
        deleted = resp.json()["deleted"]
        assert deleted["escalation_contacts"] == 1
        assert deleted["llm_usage"] == 1
        assert _rows_left_for(org_id) == {}

    def test_export_carries_the_tables_delete_would_remove(self, client):
        from app.saas.repository import EscalationContactRepository, LlmUsageRepository

        owner = _signup(client)
        org_id = owner["organization"]["id"]
        EscalationContactRepository().set_email(org_id, "legal_team", "counsel@acme.example")
        LlmUsageRepository().record(org_id=org_id, cost_usd=0.02, model="m", purpose="verify")

        bundle = client.get("/org/export", headers=_hdr(owner["access_token"])).json()
        assert [c["email"] for c in bundle["escalation_contacts"]] == ["counsel@acme.example"]
        assert [u["cost_usd"] for u in bundle["llm_usage"]] == [0.02]


class TestDeletion:
    def test_delete_requires_matching_confirm(self, client):
        owner = _signup(client)
        resp = client.request(
            "DELETE", "/org", headers=_hdr(owner["access_token"]), json={"confirm": "wrong"}
        )
        assert resp.status_code == 400

    def test_delete_purges_everything(self, client, monkeypatch):
        from app.copilot.providers.fake import FakeProvider

        monkeypatch.setattr(processing_routes, "build_provider", lambda conn: FakeProvider())
        owner = _signup(client)
        token = owner["access_token"]
        org_id = owner["organization"]["id"]
        slug = owner["organization"]["slug"]
        conn = MailboxRepository().upsert_connection(
            org_id=org_id,
            provider="fake",
            account_email="exec@acme.example",
            connected_by=owner["user"]["id"],
            access_token_enc=None,
            refresh_token_enc=None,
            token_expires_at=None,
            scopes=None,
        )
        client.post("/inbox/sync", headers=_hdr(token), json={"connection_id": conn["id"]})

        resp = client.request("DELETE", "/org", headers=_hdr(token), json={"confirm": slug})
        assert resp.status_code == 200, resp.text
        counts = resp.json()["deleted"]
        assert counts["organization"] == 1
        assert counts["users"] >= 1
        assert counts["processed_messages"] == 4

        # The session token no longer resolves (user is gone).
        assert client.get("/auth/me", headers=_hdr(token)).status_code == 401
        # And the data is really gone.
        assert MailboxRepository().get(org_id, conn["id"]) is None

    def test_delete_is_tenant_scoped(self, client):
        org1 = _signup(client, org_name="Org One")
        org2 = _signup(client, org_name="Org Two")
        # org1 deletes itself; org2 is untouched and still works.
        client.request(
            "DELETE",
            "/org",
            headers=_hdr(org1["access_token"]),
            json={"confirm": org1["organization"]["slug"]},
        )
        assert client.get("/auth/me", headers=_hdr(org2["access_token"])).status_code == 200

    def test_delete_requires_owner(self, client):
        owner = _signup(client)
        member_email = _email()
        client.post(
            "/org/members",
            headers=_hdr(owner["access_token"]),
            json={
                "email": member_email,
                "full_name": "M",
                "role": "admin",
                "temp_password": "temppass12",
            },
        )
        admin_token = client.post(
            "/auth/login", json={"email": member_email, "password": "temppass12"}
        ).json()["access_token"]
        resp = client.request(
            "DELETE",
            "/org",
            headers=_hdr(admin_token),
            json={"confirm": owner["organization"]["slug"]},
        )
        assert resp.status_code == 403  # admin is not owner
