"""Learning from the approval queue: edited approvals, routing feedback,
few-shot examples, and the insights panel. No network."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app
from app.saas.learning import MIN_DECISIONS, FeedbackService
from app.saas.repository import (
    MailboxRepository,
    ProcessedMessageRepository,
    ProposedActionRepository,
)


def _client():
    return TestClient(app)


def _signup(client) -> dict:
    resp = client.post(
        "/auth/signup",
        json={
            "email": f"fb_{uuid.uuid4().hex[:12]}@example.com",
            "password": "hunter2pass",
            "full_name": "Reviewer",
            "org_name": "Feedback Org",
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _hdr(org: dict) -> dict:
    return {"Authorization": f"Bearer {org['access_token']}"}


def _connect_fake(client, org) -> str:
    conn = MailboxRepository().upsert_connection(
        org_id=org["organization"]["id"],
        provider="fake",
        account_email=f"exec-{uuid.uuid4().hex[:8]}@fb.example",
        connected_by=org["user"]["id"],
        access_token_enc=None,
        refresh_token_enc=None,
        token_expires_at=None,
        scopes=None,
    )
    resp = client.post("/inbox/sync", headers=_hdr(org), json={"connection_id": conn["id"]})
    assert resp.status_code == 200, resp.text
    return conn["id"]


def _pending(client, org) -> list[dict]:
    resp = client.get("/inbox/actions?status=proposed", headers=_hdr(org))
    return resp.json()["actions"]


def _decide(
    org_id: str,
    action_type: str,
    sender_role: str,
    outcome: str,
    seq: int,
    confidence: float | None = None,
) -> dict:
    """Fabricate one decided action with a joined message, for aggregation tests."""
    msg = ProcessedMessageRepository().upsert(
        org_id=org_id,
        connection_id="conn-x",
        provider_message_id=f"m-{action_type}-{sender_role}-{seq}-{uuid.uuid4().hex[:6]}",
        thread_id=None,
        sender=f"{sender_role}@corp.example",
        sender_name=None,
        received_at=None,
        subject=f"Subject {seq}",
        body_preview="body",
        sender_role=sender_role,
        priority_hint="medium",
        risk_tag="none",
        deadline_minutes=240,
        business_value=1000,
    )
    actions = ProposedActionRepository()
    row = actions.create(
        org_id=org_id,
        message_id=msg["id"],
        action_type=action_type,
        content=f"Draft body {seq}",
        escalate_to=None,
        label=None,
        status="proposed",
        requires_approval=True,
        draft_source="llm",
        draft_confidence=confidence,
    )
    status = "rejected" if outcome == "rejected" else "executed"
    return actions.set_status(
        org_id,
        row["id"],
        status,
        outcome=outcome,
        decided_by="u-1",
        decided_at=f"2026-08-20T10:{seq:02d}:00+00:00",
    )


class TestEditedApproval:
    def test_edit_before_approve_records_both_texts(self):
        client = _client()
        org = _signup(client)
        _connect_fake(client, org)
        reply = next(a for a in _pending(client, org) if a["action_type"] == "reply")
        resp = client.post(
            f"/inbox/actions/{reply['id']}/approve",
            headers=_hdr(org),
            json={"content": "Thanks — I'll call you at 3pm to close this out."},
        )
        assert resp.status_code == 200, resp.text
        action = resp.json()["action"]
        assert action["status"] == "executed"
        assert action["outcome"] == "edited"
        assert action["content"] == "Thanks — I'll call you at 3pm to close this out."
        assert action["original_content"] == reply["content"]

    def test_plain_approval_stays_approved(self):
        client = _client()
        org = _signup(client)
        _connect_fake(client, org)
        reply = next(a for a in _pending(client, org) if a["action_type"] == "reply")
        resp = client.post(f"/inbox/actions/{reply['id']}/approve", headers=_hdr(org))
        assert resp.status_code == 200, resp.text
        action = resp.json()["action"]
        assert action["outcome"] == "approved"
        assert action["original_content"] is None
        assert action["content"] == reply["content"]

    def test_unchanged_content_is_not_an_edit(self):
        """Posting the draft back verbatim (what a browser form does when the
        reviewer touches nothing) must not count as a correction."""
        client = _client()
        org = _signup(client)
        _connect_fake(client, org)
        reply = next(a for a in _pending(client, org) if a["action_type"] == "reply")
        resp = client.post(
            f"/inbox/actions/{reply['id']}/approve",
            headers=_hdr(org),
            json={"content": reply["content"]},
        )
        assert resp.json()["action"]["outcome"] == "approved"


class TestInsights:
    def test_rates_trusted_and_suppressed(self):
        client = _client()
        org = _signup(client)
        org_id = org["organization"]["id"]
        for i in range(3):
            _decide(org_id, "reply", "vendor", "approved", i)
        _decide(org_id, "reply", "vendor", "edited", 3)
        for i in range(4):
            _decide(org_id, "escalate", "newsletter", "rejected", 10 + i)

        insights = FeedbackService().insights(org_id)
        assert insights["window"] == 8
        assert insights["by_action"]["reply"]["acceptance_rate"] == 1.0
        assert insights["by_action"]["escalate"]["acceptance_rate"] == 0.0
        trusted = {(t["action_type"], t["sender_role"]) for t in insights["trusted"]}
        suppressed = {(s["action_type"], s["sender_role"]) for s in insights["suppressed"]}
        assert ("reply", "vendor") in trusted
        assert ("escalate", "newsletter") in suppressed
        assert any("Stopped proposing escalate" in line for line in insights["learned"])
        assert any("edited" in line for line in insights["learned"])

    def test_too_few_decisions_change_nothing(self):
        client = _client()
        org = _signup(client)
        org_id = org["organization"]["id"]
        for i in range(MIN_DECISIONS - 1):
            _decide(org_id, "escalate", "board", "rejected", i)
        assert FeedbackService().suppressed_pairs(org_id) == {}

    def test_learning_endpoint_is_org_scoped(self):
        client = _client()
        org_a = _signup(client)
        org_b = _signup(client)
        for i in range(4):
            _decide(org_a["organization"]["id"], "escalate", "newsletter", "rejected", i)
        a = client.get("/inbox/learning", headers=_hdr(org_a)).json()
        b = client.get("/inbox/learning", headers=_hdr(org_b)).json()
        assert a["suppressed"] and a["window"] == 4
        assert b["suppressed"] == [] and b["window"] == 0


class TestFewShotExamples:
    def test_prefers_edits_and_caps_at_k(self):
        client = _client()
        org = _signup(client)
        org_id = org["organization"]["id"]
        for i in range(3):
            _decide(org_id, "reply", "vendor", "approved", i)
        _decide(org_id, "reply", "vendor", "edited", 40)
        _decide(org_id, "reply", "vendor", "rejected", 41)

        examples = FeedbackService().few_shot_examples(org_id, "reply", k=3)
        assert len(examples) == 3
        # The human-corrected draft leads; rejected drafts never appear.
        assert examples[0]["body"] == "Draft body 40"
        assert all(ex["body"] != "Draft body 41" for ex in examples)

    def test_other_action_types_excluded(self):
        client = _client()
        org = _signup(client)
        org_id = org["organization"]["id"]
        _decide(org_id, "escalate", "vendor", "approved", 0)
        assert FeedbackService().few_shot_examples(org_id, "reply") == []


class TestRoutingSuppression:
    def test_repeated_rejections_downgrade_to_deferral(self):
        """Reject the same shape of escalation MIN_DECISIONS times; the next
        sync files it as deferred — with the reason on the action — instead of
        asking a fourth time."""
        client = _client()
        org = _signup(client)
        conn_id = _connect_fake(client, org)

        for _ in range(MIN_DECISIONS):
            escalate = next(a for a in _pending(client, org) if a["action_type"] == "escalate")
            resp = client.post(
                f"/inbox/actions/{escalate['id']}/reject", headers=_hdr(org), json={}
            )
            assert resp.status_code == 200, resp.text
            # A rejected action is not "live", so a re-sync re-proposes it.
            resp = client.post("/inbox/sync", headers=_hdr(org), json={"connection_id": conn_id})
            assert resp.status_code == 200, resp.text

        last_sync = resp.json()["results"][0]
        assert last_sync.get("downgraded", 0) >= 1
        # No escalate is pending any more...
        assert all(a["action_type"] != "escalate" for a in _pending(client, org))
        # ...and the deferral says why it exists.
        listing = ProposedActionRepository().list_for_org(org["organization"]["id"])
        downgraded = [
            a
            for a in listing["actions"]
            if a["action_type"] == "defer"
            and any("Downgraded from escalate" in line for line in a["rationale"])
        ]
        assert downgraded
        assert downgraded[0]["status"] == "executed"

    def test_approved_pairs_are_not_suppressed(self):
        client = _client()
        org = _signup(client)
        conn_id = _connect_fake(client, org)
        escalate = next(a for a in _pending(client, org) if a["action_type"] == "escalate")
        client.post(f"/inbox/actions/{escalate['id']}/approve", headers=_hdr(org))
        resp = client.post("/inbox/sync", headers=_hdr(org), json={"connection_id": conn_id})
        assert resp.json()["results"][0].get("downgraded", 0) == 0


class TestCalibrationExport:
    def test_pairs_are_strict_about_correctness(self):
        """Approved-as-written = correct; edited or rejected = the stated
        confidence was wrong; drafts with no confidence are excluded."""
        client = _client()
        org = _signup(client)
        org_id = org["organization"]["id"]
        _decide(org_id, "reply", "vendor", "approved", 1, confidence=0.9)
        _decide(org_id, "reply", "vendor", "edited", 2, confidence=0.8)
        _decide(org_id, "reply", "vendor", "rejected", 3, confidence=0.7)
        _decide(org_id, "reply", "vendor", "approved", 4)  # no confidence stated

        pairs = FeedbackService().calibration_pairs(org_id)
        assert len(pairs) == 3
        by_conf = {p["confidence"]: p["correct"] for p in pairs}
        assert by_conf == {0.9: True, 0.8: False, 0.7: False}

    def test_export_script_feeds_the_calibration_cli(self, tmp_path):
        """The whole loop: decisions → export → the research repo's Brier/ECE
        math accepts the file. This is PLAN Phase 8 item 6's 'wired'."""
        import subprocess
        import sys as _sys

        import scripts.export_calibration as exporter

        client = _client()
        org = _signup(client)
        org_id = org["organization"]["id"]
        for i, conf in enumerate((0.9, 0.85, 0.6)):
            _decide(
                org_id,
                "reply",
                "vendor",
                "approved" if conf > 0.7 else "rejected",
                i,
                confidence=conf,
            )

        out = tmp_path / "pairs.json"
        assert exporter.main(["--org-slug", org["organization"]["slug"], "--out", str(out)]) == 0
        pairs = json.loads(out.read_text(encoding="utf-8"))
        assert len(pairs) == 3

        result = subprocess.run(
            [_sys.executable, "research/benchmark/calibration_cli.py", str(out), "--verbose"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        assert "Brier" in result.stdout or "brier" in result.stdout.lower()

    def test_unknown_slug_exits_nonzero(self):
        import scripts.export_calibration as exporter

        assert exporter.main(["--org-slug", "no-such-workspace-xyz"]) == 1


class TestDraftKeyCompatibility:
    def test_empty_extra_matches_the_old_scheme(self):
        """The committed demo drafts were keyed without examples; an org with
        no feedback must keep hitting them."""
        from app.llm.draft_cache import draft_key, examples_digest

        base = draft_key(provider_message_id="m-1", subject="s", body="b", action_type="reply")
        assert base == draft_key(
            provider_message_id="m-1",
            subject="s",
            body="b",
            action_type="reply",
            extra=examples_digest(None),
        )
        with_examples = draft_key(
            provider_message_id="m-1",
            subject="s",
            body="b",
            action_type="reply",
            extra=examples_digest([{"subject": "x", "body": "y"}]),
        )
        assert with_examples != base


class TestDrafterExamples:
    class _StubProvider:
        def __init__(self):
            self.captured = None

        def generate(self, messages, **kwargs):
            self.captured = messages
            return SimpleNamespace(
                content=json.dumps(
                    {"body": "Drafted reply.", "rationale": ["r"], "confidence": 0.9}
                ),
                model="stub",
                usage=None,
            )

    def test_examples_ride_along_in_the_prompt(self):
        from app.copilot.providers.base import FetchedMessage
        from app.llm.drafter import EmailDrafter

        provider = self._StubProvider()
        drafter = EmailDrafter(provider=provider)
        message = FetchedMessage(
            provider_message_id="m-1",
            thread_id="t-1",
            sender="a@b.c",
            sender_name="A",
            subject="Contract",
            body="Please confirm the renewal terms.",
        )
        result = drafter.draft(
            message=message,
            action_type="reply",
            examples=[{"subject": "Renewal", "body": "Confirmed — proceeding as agreed."}],
        )
        assert result is not None and result.body == "Drafted reply."
        user_prompt = provider.captured[-1]["content"]
        assert "APPROVED PAST DRAFTS" in user_prompt
        assert "Confirmed — proceeding as agreed." in user_prompt
        # The message itself still leads the prompt.
        assert user_prompt.index("Please confirm the renewal terms.") < user_prompt.index(
            "APPROVED PAST DRAFTS"
        )

    def test_no_examples_means_no_examples_section(self):
        provider = self._StubProvider()
        from app.copilot.providers.base import FetchedMessage
        from app.llm.drafter import EmailDrafter

        EmailDrafter(provider=provider).draft(
            message=FetchedMessage(
                provider_message_id="m-1",
                thread_id="t",
                sender="a@b.c",
                sender_name="",
                subject="s",
                body="b",
            ),
            action_type="reply",
        )
        assert "APPROVED PAST DRAFTS" not in provider.captured[-1]["content"]
