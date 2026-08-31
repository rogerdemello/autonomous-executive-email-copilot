"""Commitment tracking: the capability the market rewards and nobody ships.

Two halves are tested here. The extractor is a pure function over prose, so it
is tested against fixed text with a fixed clock. The plumbing — sync, approve,
the "Waiting on" page — is tested end to end through the demo mailbox.

The bar throughout is precision over recall. A follow-up list that surfaces
every sentence containing "will" is noise, and noise here is fatal: the whole
value is that a short list means something, so a false positive costs more
than a miss.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.copilot import commitments as extractor
from app.main import app

# A Sunday, so "Friday" resolves forward and weekday arithmetic is visible.
NOW = datetime(2026, 8, 16, 9, 0, tzinfo=timezone.utc)


class TestFindsRealPromises:
    @pytest.mark.parametrize(
        "sentence",
        [
            "I'll send the revised deck on Friday.",
            "We will circulate the board pack tomorrow.",
            "Let me confirm the numbers with finance and revert.",
            "I'm going to pull the Q3 figures together this week.",
            "We are waiting on legal before we can sign.",
            "I've asked the team to prepare the migration plan.",
        ],
    )
    def test_a_promise_is_found(self, sentence):
        found = extractor.extract(sentence, direction=extractor.THEIRS, now=NOW)
        assert len(found) == 1
        assert found[0].text == sentence

    def test_the_sentence_is_kept_verbatim(self):
        """A row you cannot check at a glance sends you back to the thread,
        which is the work this surface exists to remove."""
        body = "Thanks for the update.\n\nI'll have the signed copy back by Thursday.\n\nBest, Sam"
        found = extractor.extract(body, direction=extractor.THEIRS, now=NOW)
        assert [f.text for f in found] == ["I'll have the signed copy back by Thursday."]


class TestIgnoresNonPromises:
    @pytest.mark.parametrize(
        "sentence",
        [
            "I'll be in touch.",
            "We look forward to hearing from you.",
            "Let me know if you need anything else.",
            "They will send the invoice next week.",
            "I won't be able to make Thursday.",
            "We will not be proceeding.",
            "The renewal will auto-continue unless cancelled.",
        ],
    )
    def test_boilerplate_negations_and_third_parties_are_skipped(self, sentence):
        assert extractor.extract(sentence, direction=extractor.THEIRS, now=NOW) == []

    def test_a_paragraph_is_not_a_promise(self):
        """Length is the cheapest signal that a 'promise' is really context
        that happens to contain a modal verb."""
        long_one = "I will " + ("consider the wider implications of this at length, " * 12)
        assert extractor.extract(long_one, direction=extractor.THEIRS, now=NOW) == []


class TestDates:
    @pytest.mark.parametrize(
        ("sentence", "expected"),
        [
            ("I'll send it tomorrow.", "2026-08-17"),
            ("I'll send it today.", "2026-08-16"),
            ("I'll send it on Friday.", "2026-08-21"),
            ("I'll send it next week.", "2026-08-23"),
            ("I'll send it in 3 days.", "2026-08-19"),
            ("I'll send it by 25 September.", "2026-09-25"),
            ("I'll send it by September 25.", "2026-09-25"),
            ("I'll send it by 2026-12-01.", "2026-12-01"),
            ("I'll send it by end of week.", "2026-08-21"),
        ],
    )
    def test_a_date_phrase_resolves(self, sentence, expected):
        found = extractor.extract(sentence, direction=extractor.THEIRS, now=NOW)
        assert found[0].due_at == expected

    def test_the_phrase_is_kept_beside_the_date(self):
        """Showing the words the sender used is what lets a reviewer catch a
        bad resolution rather than trust it."""
        found = extractor.extract("I'll send it on Friday.", direction=extractor.THEIRS, now=NOW)
        assert found[0].due_phrase.lower() == "friday"

    def test_a_weekday_never_resolves_to_today(self):
        """A promise for a day three-quarters gone is not what anyone meant."""
        sunday = extractor.extract("I'll send it Sunday.", direction=extractor.THEIRS, now=NOW)
        assert sunday[0].due_at == "2026-08-23"

    def test_no_date_is_left_undated(self):
        """Guessing a deadline nobody stated and then nagging about it is the
        same failure the draft verifier exists to catch, one surface over."""
        found = extractor.extract("I'll get you the deck.", direction=extractor.THEIRS, now=NOW)
        assert found[0].due_at is None
        assert not extractor.is_overdue(found[0].due_at, now=NOW)

    def test_a_past_date_is_overdue(self):
        assert extractor.is_overdue("2026-08-15", now=NOW)
        assert not extractor.is_overdue("2026-08-16", now=NOW)


class TestDirection:
    def test_an_incoming_request_is_something_you_owe(self):
        """Nobody promised it, but it is unambiguously yours now."""
        found = extractor.extract(
            "Please send the figures by Thursday.",
            direction=extractor.THEIRS,
            now=NOW,
            include_requests=True,
        )
        assert found[0].direction == extractor.OURS
        assert found[0].due_at == "2026-08-20"

    def test_requests_are_off_by_default(self):
        """In your own outgoing reply, "please send X" is a request you made,
        not a commitment you took on."""
        assert (
            extractor.extract("Please send the figures.", direction=extractor.OURS, now=NOW) == []
        )

    def test_your_own_promise_stays_yours(self):
        found = extractor.extract(
            "We'll have a decision for you by Thursday.", direction=extractor.OURS, now=NOW
        )
        assert found[0].direction == extractor.OURS


# --------------------------------------------------------------------------- #
# End to end, through the product
# --------------------------------------------------------------------------- #
def _demo_workspace():
    client = TestClient(app, follow_redirects=False)
    page = client.get("/signup").text
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    email = f"c_{uuid.uuid4().hex[:10]}@commit.example"
    assert (
        client.post(
            "/signup",
            data={
                "csrf_token": csrf,
                "org_name": "Commitment Co",
                "full_name": "C",
                "email": email,
                "password": "a-strong-password",
            },
        ).status_code
        == 303
    )
    page = client.get("/app/connect").text
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    assert client.post("/app/connect/demo", data={"csrf_token": csrf}).status_code == 303

    from app.saas.repository import UserRepository

    return client, UserRepository().get_by_email_global(email)["org_id"]


def _csrf(client, path="/app/waiting"):
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get(path).text).group(1)


class TestThroughTheProduct:
    def test_a_sync_records_what_the_mailbox_promised(self):
        from app.saas.repository import CommitmentRepository

        _client, org_id = _demo_workspace()
        listing = CommitmentRepository().list_for_org(org_id)
        assert listing["total"] > 0, "the demo mailbox contains promises"
        assert all(c["status"] == "open" for c in listing["commitments"])

    def test_spam_is_never_a_commitment(self):
        """ "Subscribe now and we will register your team" parses as a
        perfectly good promise. Two of those in a seven-row list is enough to
        make nobody open it again."""
        from app.saas.repository import CommitmentRepository

        _client, org_id = _demo_workspace()
        rows = CommitmentRepository().list_for_org(org_id, limit=200)["commitments"]
        assert rows
        assert not any("subscribe" in c["text"].lower() for c in rows)

    def test_re_syncing_does_not_duplicate(self):
        """A list that grows a copy of every row on each sweep is a list
        nobody opens twice."""
        from app.saas.repository import CommitmentRepository

        client, org_id = _demo_workspace()
        before = CommitmentRepository().list_for_org(org_id, limit=200)["total"]

        csrf = _csrf(client, "/app/inbox")
        assert client.post("/app/sync", data={"csrf_token": csrf}).status_code == 303
        assert CommitmentRepository().list_for_org(org_id, limit=200)["total"] == before

    def test_the_page_shows_the_sentence_and_the_direction(self):
        client, _org_id = _demo_workspace()
        body = client.get("/app/waiting").text
        assert "waiting on" in body
        assert "Mark done" in body

    def test_marking_done_moves_it_off_the_open_list(self):
        from app.saas.repository import CommitmentRepository

        client, org_id = _demo_workspace()
        repo = CommitmentRepository()
        target = repo.list_for_org(org_id, limit=200)["commitments"][0]

        response = client.post(
            f"/app/commitments/{target['id']}/done", data={"csrf_token": _csrf(client)}
        )
        assert response.status_code == 303
        assert target["id"] not in [c["id"] for c in repo.list_for_org(org_id)["commitments"]]

        done = repo.list_for_org(org_id, status="done")["commitments"]
        assert target["id"] in [c["id"] for c in done]

    def test_dismissing_is_distinct_from_done(self):
        """ "Not a commitment" is the feedback that keeps the list worth
        reading; conflating it with "done" throws that signal away."""
        from app.saas.repository import CommitmentRepository

        client, org_id = _demo_workspace()
        repo = CommitmentRepository()
        target = repo.list_for_org(org_id, limit=200)["commitments"][0]

        client.post(f"/app/commitments/{target['id']}/dismiss", data={"csrf_token": _csrf(client)})
        assert repo.get(org_id, target["id"])["status"] == "dropped"

    def test_a_resolved_commitment_is_not_reopened_by_a_re_sync(self):
        from app.saas.repository import CommitmentRepository

        client, org_id = _demo_workspace()
        repo = CommitmentRepository()
        target = repo.list_for_org(org_id, limit=200)["commitments"][0]
        client.post(f"/app/commitments/{target['id']}/done", data={"csrf_token": _csrf(client)})

        csrf = _csrf(client, "/app/inbox")
        client.post("/app/sync", data={"csrf_token": csrf})
        assert repo.get(org_id, target["id"])["status"] == "done"

    def test_an_unknown_commitment_is_a_404_not_a_500(self):
        client, _org_id = _demo_workspace()
        response = client.post(
            "/app/commitments/no-such-id/done", data={"csrf_token": _csrf(client)}
        )
        assert response.status_code == 404

    def test_resolving_needs_a_csrf_token(self):
        client, org_id = _demo_workspace()
        from app.saas.repository import CommitmentRepository

        target = CommitmentRepository().list_for_org(org_id, limit=200)["commitments"][0]
        assert client.post(f"/app/commitments/{target['id']}/done", data={}).status_code == 403

    def test_approving_a_reply_records_what_you_just_promised(self):
        """The half of follow-up tracking only the sending party can see."""
        from app.saas.repository import CommitmentRepository, ProposedActionRepository

        client, org_id = _demo_workspace()
        actions = ProposedActionRepository()
        target = next(
            a
            for a in actions.list_for_org(org_id, status="proposed", limit=200)["actions"]
            if a["action_type"] == "reply"
        )
        csrf = _csrf(client, "/app/approvals")
        response = client.post(
            f"/app/actions/{target['id']}/approve",
            data={
                "csrf_token": csrf,
                "content": "Understood. I'll have the signed copy back to you by Thursday.",
            },
        )
        assert response.status_code == 303

        ours = CommitmentRepository().list_for_org(org_id, direction="ours", limit=200)
        assert any("signed copy" in c["text"] for c in ours["commitments"])

    def test_the_sidebar_counts_what_is_outstanding(self):
        client, _org_id = _demo_workspace()
        assert "Waiting on" in client.get("/app/inbox").text

    def test_commitments_are_exported_and_erased_with_the_workspace(self):
        """The data-lifecycle module says to add every new tenant table here,
        and a follow-up list that survives a GDPR delete is a breach."""
        from app.saas.data_lifecycle import DataLifecycleService
        from app.saas.repository import CommitmentRepository

        _client, org_id = _demo_workspace()
        bundle = DataLifecycleService().export_org(org_id)
        assert bundle["commitments"], "commitments belong in the export"

        DataLifecycleService().delete_org(org_id)
        assert CommitmentRepository().list_for_org(org_id, limit=200)["total"] == 0
