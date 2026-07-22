"""End-to-end test for the real-inbox pipeline (Track A, Phase 1).

Drives signup -> connect(fake) -> sync -> proposed actions persisted -> approve
(executed, side effect on the shared FakeProvider) -> reject, plus tenant
isolation and enrichment/pipeline unit checks. No network, no browser — the
provider is the in-memory FakeProvider, injected by monkeypatching the route's
`build_provider` (same discipline as test_saas_mailbox.py's OAuth patching).
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from env.api import app
from env.product import enrich, pipeline
from env.product.providers.fake import FakeProvider, default_fixture_messages
from env.saas import processing_routes
from env.saas.repository import MailboxRepository


@pytest.fixture
def client():
    return TestClient(app)


def _email() -> str:
    return f"admin_{uuid.uuid4().hex[:12]}@example.com"


def _signup(client):
    resp = client.post(
        "/auth/signup",
        json={
            "email": _email(),
            "password": "hunter2pass",
            "full_name": "Admin",
            "org_name": "Acme",
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _hdr(token):
    return {"Authorization": f"Bearer {token}"}


def _seed_connection(org_id: str, user_id: str, account_email: str = "exec@acme.example") -> str:
    """Create a mailbox connection row directly (no OAuth needed for the fake)."""
    conn = MailboxRepository().upsert_connection(
        org_id=org_id,
        provider="fake",
        account_email=account_email,
        connected_by=user_id,
        access_token_enc=None,
        refresh_token_enc=None,
        token_expires_at=None,
        scopes=None,
    )
    return conn["id"]


# --------------------------------------------------------------------------- #
# Unit: enrichment + pipeline (gold-free decision core)
# --------------------------------------------------------------------------- #
class TestEnrichAndPipeline:
    def test_enrichment_makes_ranking_non_inert(self):
        obs = enrich.to_observation(default_fixture_messages(), account_email="exec@acme.example")
        priorities = {e.id: e.priority_hint for e in obs.emails}
        # The legal + urgent messages must rank above spam/normal (not all-medium).
        assert priorities["m-legal-1"] == "high"
        assert priorities["m-urgent-1"] == "high"
        assert priorities["m-spam-1"] != "high"
        risks = {e.id: e.risk_tag for e in obs.emails}
        assert risks["m-legal-1"] == "legal"

    def test_sender_role_inference(self):
        assert enrich.infer_sender_role("bob@acme.com", "alice@acme.com") == "internal"
        assert enrich.infer_sender_role("noreply@service.io", "alice@acme.com") == "vendor"
        assert enrich.infer_sender_role("someone@gmail.com", "alice@acme.com") == "client"
        assert enrich.infer_sender_role("x@random.io", "alice@acme.com") == "unknown"

    def test_pipeline_terminates_and_proposes(self):
        obs = enrich.to_observation(default_fixture_messages(), account_email="exec@acme.example")
        actions = pipeline.run_policy(obs)
        assert actions  # produced something
        proposals = pipeline.to_proposals(actions)
        approval = [p for p in proposals if p.requires_approval]
        # A legal escalation and an urgent reply both need approval.
        types = {p.action_type for p in approval}
        assert "escalate" in types
        assert "reply" in types


# --------------------------------------------------------------------------- #
# Integration: full journey
# --------------------------------------------------------------------------- #
class TestInboxJourney:
    def test_sync_approve_reject(self, client, monkeypatch):
        shared = FakeProvider()  # one instance so we can assert its side effects
        monkeypatch.setattr(processing_routes, "build_provider", lambda conn: shared)

        data = _signup(client)
        token = data["access_token"]
        conn_id = _seed_connection(data["organization"]["id"], data["user"]["id"])

        # Sync
        resp = client.post("/inbox/sync", headers=_hdr(token), json={"connection_id": conn_id})
        assert resp.status_code == 200, resp.text
        result = resp.json()["results"][0]
        assert result["messages"] == 4
        assert result["proposed"] == 2  # reply + escalate held for approval
        assert result["auto_executed"] == 5  # 4 classify + 1 defer auto-labeled

        # Auto-executed labels landed on the mailbox.
        assert len(shared.labels) == 5

        # Messages persisted + tenant-scoped
        msgs = client.get("/inbox/messages", headers=_hdr(token)).json()["messages"]
        assert len(msgs) == 4
        assert any(m["risk_tag"] == "legal" for m in msgs)

        # last_synced_at now set
        conns = client.get("/mailbox/connections", headers=_hdr(token)).json()["connections"]
        assert conns[0]["last_synced_at"] is not None

        # Actions: 7 total, 2 proposed
        all_actions = client.get("/inbox/actions", headers=_hdr(token)).json()["actions"]
        assert len(all_actions) == 7
        proposed = client.get("/inbox/actions?status=proposed", headers=_hdr(token)).json()[
            "actions"
        ]
        assert len(proposed) == 2

        reply = next(a for a in proposed if a["action_type"] == "reply")
        escalate = next(a for a in proposed if a["action_type"] == "escalate")

        # Approve the reply -> executed, provider sent it
        ap = client.post(f"/inbox/actions/{reply['id']}/approve", headers=_hdr(token))
        assert ap.status_code == 200, ap.text
        assert ap.json()["action"]["status"] == "executed"
        assert ap.json()["action"]["outcome"] == "approved"
        assert len(shared.sent) == 1

        # Approving again is a conflict
        assert (
            client.post(f"/inbox/actions/{reply['id']}/approve", headers=_hdr(token)).status_code
            == 409
        )

        # Reject the escalation -> rejected, no draft created
        rj = client.post(
            f"/inbox/actions/{escalate['id']}/reject",
            headers=_hdr(token),
            json={"comment": "not now"},
        )
        assert rj.status_code == 200
        assert rj.json()["action"]["status"] == "rejected"
        assert rj.json()["action"]["outcome"] == "rejected"
        assert len(shared.drafts) == 0

    def test_tenant_isolation(self, client, monkeypatch):
        monkeypatch.setattr(processing_routes, "build_provider", lambda conn: FakeProvider())
        org1 = _signup(client)
        org2 = _signup(client)
        conn1 = _seed_connection(org1["organization"]["id"], org1["user"]["id"])
        client.post(
            "/inbox/sync", headers=_hdr(org1["access_token"]), json={"connection_id": conn1}
        )

        m1 = client.get("/inbox/messages", headers=_hdr(org1["access_token"])).json()["messages"]
        m2 = client.get("/inbox/messages", headers=_hdr(org2["access_token"])).json()["messages"]
        assert len(m1) == 4
        assert m2 == []
        a2 = client.get("/inbox/actions", headers=_hdr(org2["access_token"])).json()["actions"]
        assert a2 == []

    def test_member_cannot_sync(self, client, monkeypatch):
        monkeypatch.setattr(processing_routes, "build_provider", lambda conn: FakeProvider())
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
        conn = _seed_connection(owner["organization"]["id"], owner["user"]["id"])
        resp = client.post("/inbox/sync", headers=_hdr(member_token), json={"connection_id": conn})
        assert resp.status_code == 403
        # ...but a member can view
        assert client.get("/inbox/messages", headers=_hdr(member_token)).status_code == 200

    def test_resync_is_idempotent(self, client, monkeypatch):
        # Bug 1a: a second sync must NOT duplicate actions or re-fire provider writes.
        shared = FakeProvider()
        monkeypatch.setattr(processing_routes, "build_provider", lambda conn: shared)
        data = _signup(client)
        token = data["access_token"]
        conn_id = _seed_connection(data["organization"]["id"], data["user"]["id"])

        first = client.post(
            "/inbox/sync", headers=_hdr(token), json={"connection_id": conn_id}
        ).json()["results"][0]
        assert first["proposed"] == 2
        assert first["auto_executed"] == 5
        assert first.get("skipped", 0) == 0
        labels_after_first = len(shared.labels)
        actions_after_first = len(
            client.get("/inbox/actions", headers=_hdr(token)).json()["actions"]
        )

        second = client.post(
            "/inbox/sync", headers=_hdr(token), json={"connection_id": conn_id}
        ).json()["results"][0]
        # Everything already live → all skipped, nothing new created or written.
        assert second["proposed"] == 0
        assert second["auto_executed"] == 0
        assert second["skipped"] == 7
        assert len(shared.labels) == labels_after_first  # no duplicate provider writes
        actions_after_second = len(
            client.get("/inbox/actions", headers=_hdr(token)).json()["actions"]
        )
        assert actions_after_second == actions_after_first  # no duplicate actions

    def test_provider_write_failure_does_not_abort_sync(self, client, monkeypatch):
        # Bug 1b: a failing auto-label write marks that action failed but the sync
        # still completes and records last_synced_at.
        class FailingLabelProvider(FakeProvider):
            def add_label(self, provider_message_id, label):
                from env.product.providers.base import WriteResult

                return WriteResult(ok=False, detail="simulated provider outage")

        provider = FailingLabelProvider()
        monkeypatch.setattr(processing_routes, "build_provider", lambda conn: provider)
        data = _signup(client)
        token = data["access_token"]
        conn_id = _seed_connection(data["organization"]["id"], data["user"]["id"])

        resp = client.post("/inbox/sync", headers=_hdr(token), json={"connection_id": conn_id})
        assert resp.status_code == 200, resp.text  # not a 500
        # The auto-label actions are recorded as failed, not executed.
        failed = client.get("/inbox/actions?status=failed", headers=_hdr(token)).json()["actions"]
        assert len(failed) == 5
        # Sync still finished: last_synced_at was written.
        conns = client.get("/mailbox/connections", headers=_hdr(token)).json()["connections"]
        assert conns[0]["last_synced_at"] is not None
        # A failed action is retryable on the next sync (not treated as "live").
        second = client.post(
            "/inbox/sync", headers=_hdr(token), json={"connection_id": conn_id}
        ).json()["results"][0]
        assert second["auto_executed"] == 5  # retried, not skipped
