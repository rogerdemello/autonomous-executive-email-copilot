"""Draft-then-verify: the second pass that checks prose against its source
before it enters the approval queue. No network — the model layer is stubbed."""

from __future__ import annotations

import re
import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.copilot.providers.base import FetchedMessage
from app.llm.verifier import FLAGGED, VERIFIED, verify_draft
from app.main import app


def _message(body: str = "They are seeking £480k over the Q2 credits.") -> FetchedMessage:
    return FetchedMessage(
        provider_message_id="m-1",
        thread_id="t-1",
        sender="j.whitfield@counsel.example",
        sender_name="James Whitfield",
        subject="Calloway claim",
        body=body,
    )


class TestDeterministicLayer:
    def test_grounded_draft_is_verified(self):
        status, notes = verify_draft(
            "Confirmed — the £480k claim goes to counsel today; expect our position shortly.",
            message=_message(),
            action_type="reply",
        )
        assert status == VERIFIED
        assert notes == []

    def test_invented_number_is_flagged_with_a_note(self):
        status, notes = verify_draft(
            "Confirmed — we will settle the £520k claim tomorrow at 09:30.",
            message=_message(),
            action_type="reply",
        )
        assert status == FLAGGED
        assert any("520" in note for note in notes)

    def test_soft_rubric_warnings_do_not_flag(self):
        """A handover addressed to the sender is a legitimate pattern; it must
        not put a warning chip on every escalation back to outside counsel."""
        status, _ = verify_draft(
            "James, please take this £480k claim forward and recommend settle or defend.",
            message=_message(),
            action_type="escalate",
        )
        assert status == VERIFIED


class TestModelLayer:
    def test_unsupported_claims_from_the_model_are_appended(self, monkeypatch):
        from app.llm import verifier

        stub = SimpleNamespace(
            generate=lambda messages, **kw: SimpleNamespace(
                content='{"unsupported": ["a 3pm call was never proposed"]}'
            )
        )
        monkeypatch.setattr("app.llm.providers.auto_detect_provider", lambda: stub, raising=True)
        status, notes = verifier.verify_draft(
            "Confirmed — the £480k claim is with counsel; let's speak at our usual call.",
            message=_message(),
            action_type="reply",
            live_llm=True,
        )
        assert status == FLAGGED
        assert any("unsupported claim" in note for note in notes)

    def test_model_failure_degrades_to_the_deterministic_verdict(self, monkeypatch):
        from app.llm import verifier

        def boom():
            raise RuntimeError("no key")

        monkeypatch.setattr("app.llm.providers.auto_detect_provider", boom, raising=True)
        status, notes = verifier.verify_draft(
            "Confirmed — the £480k claim goes to counsel today.",
            message=_message(),
            action_type="reply",
            live_llm=True,
        )
        assert status == VERIFIED
        assert notes == []


class TestEndToEnd:
    def _demo_workspace(self):
        client = TestClient(app, follow_redirects=False)
        page = client.get("/signup").text
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        resp = client.post(
            "/signup",
            data={
                "csrf_token": csrf,
                "org_name": "Verify Org",
                "full_name": "V",
                "email": f"v_{uuid.uuid4().hex[:10]}@verify.example",
                "password": "a-strong-password",
            },
        )
        assert resp.status_code == 303
        page = client.get("/app/connect").text
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        resp = client.post("/app/connect/demo", data={"csrf_token": csrf})
        assert resp.status_code == 303
        return client

    def test_demo_sync_verifies_every_held_draft(self):
        """The seeded demo queue carries real verification verdicts — including
        the true positive: the model's invented '25 September' deadline."""
        client = self._demo_workspace()
        html = client.get("/app/approvals").text
        assert ">verified</span>" in html
        assert ">check flagged</span>" in html
        assert "25" in html  # the flagged note names the invented number
