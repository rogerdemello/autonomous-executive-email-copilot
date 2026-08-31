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


class TestEvidence:
    """A verdict tells a reviewer to go hunting. Evidence gives them a
    decision: the sentence in the draft, and the line of the source it failed
    against. That difference is the product."""

    def test_an_invented_number_names_the_sentence_it_is_in(self):
        verdict = verify_draft(
            "Confirmed. We will settle the £520k claim tomorrow at 09:30.",
            message=_message(),
            action_type="reply",
        )
        assert verdict.flagged
        finding = next(f for f in verdict.findings if f.kind == "invented_number")
        assert "520" in finding.claim
        assert finding.claim != "520", "the claim must be the sentence, not the bare figure"
        # And the reviewer is pointed at what the source *did* say with numbers.
        assert finding.source and "480" in finding.source

    def test_a_finding_says_when_the_source_is_simply_silent(self):
        verdict = verify_draft(
            "Helena, the £480k claim goes to counsel today.",
            message=_message(),
            action_type="reply",
        )
        assert verdict.flagged
        finding = next(f for f in verdict.findings if f.kind == "ungrounded_greeting")
        assert "Helena" in finding.claim
        assert finding.source is None

    def test_a_verified_draft_has_nothing_to_show(self):
        verdict = verify_draft(
            "Confirmed — the £480k claim goes to counsel today.",
            message=_message(),
            action_type="reply",
        )
        assert verdict.status == VERIFIED
        assert verdict.findings == []

    def test_the_verdict_still_unpacks_as_a_pair(self):
        """The two-tuple was this function's contract before findings existed
        and is still a fine way to ask the narrow question."""
        status, notes = verify_draft(
            "Confirmed — the £480k claim goes to counsel today.",
            message=_message(),
            action_type="reply",
        )
        assert status == VERIFIED
        assert notes == []


class TestModelLayer:
    def test_unsupported_claims_from_the_model_carry_their_evidence(self, monkeypatch):
        from app.llm import verifier

        payload = (
            '{"unsupported": [{"claim": "Let us speak at our usual call.",'
            ' "source": "They are seeking £480k over the Q2 credits.",'
            ' "why": "no call was proposed"}]}'
        )
        stub = SimpleNamespace(generate=lambda messages, **kw: SimpleNamespace(content=payload))
        monkeypatch.setattr("app.llm.providers.auto_detect_provider", lambda: stub, raising=True)
        verdict = verifier.verify_draft(
            "Confirmed — the £480k claim is with counsel. Let us speak at our usual call.",
            message=_message(),
            action_type="reply",
            live_llm=True,
        )
        assert verdict.flagged
        finding = next(f for f in verdict.findings if f.kind == "unsupported_claim")
        assert finding.claim == "Let us speak at our usual call."
        assert finding.detail == "no call was proposed"
        assert finding.source == "They are seeking £480k over the Q2 credits."

    def test_a_model_quoting_a_source_line_that_does_not_exist_is_not_trusted(self, monkeypatch):
        """Trust the model for the judgement, not for the quoting."""
        from app.llm import verifier

        payload = (
            '{"unsupported": [{"claim": "We agreed a 3pm call.",'
            ' "source": "I will call you at 3pm on Thursday.", "why": "invented"}]}'
        )
        stub = SimpleNamespace(generate=lambda messages, **kw: SimpleNamespace(content=payload))
        monkeypatch.setattr("app.llm.providers.auto_detect_provider", lambda: stub, raising=True)
        verdict = verifier.verify_draft(
            "The £480k claim is with counsel. We agreed a 3pm call.",
            message=_message(),
            action_type="reply",
            live_llm=True,
        )
        finding = next(f for f in verdict.findings if f.kind == "unsupported_claim")
        assert finding.source is None

    def test_the_older_bare_string_shape_still_degrades_to_a_finding(self, monkeypatch):
        """A model that ignores half the instruction should give a weaker
        finding, not no verification at all."""
        from app.llm import verifier

        stub = SimpleNamespace(
            generate=lambda messages, **kw: SimpleNamespace(
                content='{"unsupported": ["a 3pm call was never proposed"]}'
            )
        )
        monkeypatch.setattr("app.llm.providers.auto_detect_provider", lambda: stub, raising=True)
        verdict = verifier.verify_draft(
            "Confirmed — the £480k claim is with counsel; let's speak at our usual call.",
            message=_message(),
            action_type="reply",
            live_llm=True,
        )
        assert verdict.flagged
        assert any("unsupported claim" in note for note in verdict.notes)
        assert verdict.findings[-1].claim == "a 3pm call was never proposed"

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

    def test_the_queue_shows_the_source_line_a_claim_failed_against(self):
        """The demo moment ("invented 25 September") as a daily workflow: the
        reviewer sees the sentence and what the source actually said."""
        client = self._demo_workspace()
        html = client.get("/app/approvals").text
        assert "to look at before you send this" in html
        assert "the source says" in html
        assert "Remove this sentence" in html

    def test_verification_totals_appear_on_every_signed_in_page(self):
        """The number no competitor can quote was rendering as one chip on one
        page."""
        client = self._demo_workspace()
        for path in ("/app/inbox", "/app/approvals", "/app/settings"):
            body = client.get(path).text
            assert "drafts verified against their source" in body, path

    def test_stored_claims_survive_the_round_trip(self):
        from app.saas.repository import ProposedActionRepository, UserRepository

        client = self._demo_workspace()
        me = client.get("/app/settings")
        assert me.status_code == 200

        org_id = UserRepository().get_by_email_global(
            re.search(r"([\w.+-]+@verify\.example)", me.text).group(1)
        )["org_id"]
        actions = ProposedActionRepository().list_for_org(org_id, limit=200)["actions"]
        flagged = [a for a in actions if a["verification_status"] == "flagged"]
        assert flagged, "the demo must flag at least one draft"
        claims = flagged[0]["verification_claims"]
        assert claims and isinstance(claims, list)
        assert {"kind", "detail", "claim", "source"} <= set(claims[0])
