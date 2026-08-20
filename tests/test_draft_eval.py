"""Draft-quality rubric: unit checks, the committed-corpus baseline, the gate.

The committed demo drafts are a real graded corpus: 10/11 pass, and the one
flag is genuine (the model wrote "by 25 September" for a message whose deadline
is 30 September). These tests pin that baseline so a regenerated cache that
gets *worse* fails CI, and so nobody quietly tunes the rubric until its one
true positive disappears.
"""

from __future__ import annotations

from app.llm.draft_eval import evaluate_cache, evaluate_draft


class TestChecks:
    def test_grounded_draft_passes(self):
        verdict = evaluate_draft(
            "Confirmed — the £480k claim lands with counsel today; expect our position by Friday.",
            subject="Calloway claim",
            source_body="They are seeking £480k over the Q2 credits.",
        )
        assert verdict["passed"], verdict

    def test_invented_number_is_flagged(self):
        verdict = evaluate_draft(
            "I'll wire the $75,000 tomorrow morning as agreed.",
            subject="Payment",
            source_body="Please confirm the invoice amount of $57,000.",
        )
        assert not verdict["passed"]
        assert "no_invented_numbers" in verdict["flags"]

    def test_grounded_greeting_passes_and_ungrounded_fails(self):
        source = {
            "subject": "Renewal",
            "source_body": "See attached.",
            "sender_name": "Helena Ruiz",
        }
        ok = evaluate_draft("Helena, thanks — reviewing the attached renewal terms now.", **source)
        assert ok["passed"]
        bad = evaluate_draft("Marcus, thanks — reviewing the attached renewal terms now.", **source)
        assert "greeting_is_grounded" in bad["flags"]

    def test_assistant_self_reference_is_flagged(self):
        verdict = evaluate_draft(
            "As an AI language model I cannot commit to the meeting, but the terms look fine.",
            source_body="Can you meet Tuesday?",
        )
        assert "no_assistant_self_reference" in verdict["flags"]

    def test_risky_content_is_flagged(self):
        verdict = evaluate_draft(
            "We should simply bypass security review and ship it — nobody will check the logs.",
            source_body="Release checklist attached.",
        )
        assert "no_risky_content" in verdict["flags"]

    def test_too_short_is_flagged(self):
        assert "length_within_bounds" in evaluate_draft("Noted.", source_body="x")["flags"]

    def test_handover_to_sender_is_a_warning_not_a_failure(self):
        """The escalation target can legitimately BE the sender (outside
        counsel writes in; the handover goes back to them)."""
        verdict = evaluate_draft(
            "James, please take this forward and give me a recommendation this week.",
            source_body="Calloway filed a claim this morning.",
            sender_name="James Whitfield",
            action_type="escalate",
        )
        assert verdict["passed"]
        assert "handover_not_addressed_to_sender" in verdict["warnings"]


class TestCommittedCorpus:
    def test_baseline_is_ten_of_eleven(self):
        """Every committed draft matches a fixture message and 10/11 pass."""
        report = evaluate_cache()
        assert report["drafts"] == 11
        assert report["unmatched_cache_entries"] == 0
        assert report["passed"] == 10

    def test_the_one_flag_is_the_real_invented_deadline(self):
        """The rubric's single flag on our own corpus is a true positive: the
        model invented '25 September' for a 30 September deadline. If this
        test starts failing because the flag vanished, either the draft was
        regenerated (fine — update the baseline) or the rubric was blunted
        (not fine)."""
        report = evaluate_cache()
        flagged = [r for r in report["results"] if not r["passed"]]
        assert len(flagged) == 1
        assert flagged[0]["provider_message_id"] == "m-legal-policy-review"
        assert flagged[0]["flags"] == ["no_invented_numbers"]


class TestGate:
    def test_default_gate_passes_and_a_perfect_gate_fails(self):
        import scripts.eval_drafts as cli

        assert cli.main([]) == 0
        assert cli.main(["--min-pass", "1.0"]) == 1

    def test_json_report_is_written(self, tmp_path):
        import json

        import scripts.eval_drafts as cli

        out = tmp_path / "report.json"
        assert cli.main(["--json", str(out)]) == 0
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["drafts"] == 11 and report["passed"] == 10
