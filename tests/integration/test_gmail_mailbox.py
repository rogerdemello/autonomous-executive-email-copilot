"""A real Gmail account, end to end, through the product.

Nothing is stubbed above ``httpx``: the connection is made by the OAuth callback,
the provider is built by :mod:`app.saas.provider_factory` out of the encrypted
token it stored, the sync is the one the Sync button and the background worker
both call, and every assertion is either a row the product wrote or a change in
the mailbox at the other end of the wire.

Each class below is a failure this product actually shipped, and every one of
them was green against ``DemoProvider``.
"""

from __future__ import annotations

from datetime import datetime

from app.saas.provider_factory import build_provider
from app.saas.sync_service import InboxSyncService

from .conftest import google_wire


class TestTheFirstSyncOfARealMailbox:
    """Connect, then wait. The queue has to fill without anyone clicking."""

    def test_the_queue_fills_without_anyone_pressing_sync(self, workspace, connect):
        wire = google_wire()
        connect(wire, "google")

        assert len(workspace.rows()) == 5
        assert workspace.held("reply")["action_type"] == "reply"
        # Not the "reading your mailbox" holding screen: it has been read.
        assert "Reading your mailbox" not in workspace.inbox()

    def test_reading_the_mailbox_costs_one_list_call_and_one_fetch_per_message(
        self, workspace, connect
    ):
        """Gmail's list endpoint returns ids only, so a sync is N+1 by
        construction. That is the number that decides whether a 100-message
        first sync can happen inside an HTTP request — it cannot, which is why
        it does not."""
        wire = google_wire()
        connect(wire, "google")

        assert wire.call_count("GET", "/messages?") == 1
        fetches = [url for method, url in wire.calls if method == "GET" and "/messages/" in url]
        assert len(fetches) == 5
        assert all("format=full" in url for url in fetches)

    def test_only_the_newest_mail_is_read_when_the_mailbox_is_capped(
        self, workspace, connect, monkeypatch
    ):
        """A sweep is capped, so the cap has to take the top of the mailbox.
        Taking the bottom means syncing someone's history forever and never
        reaching the message they are waiting on."""
        monkeypatch.setenv("INBOX_SYNC_LIMIT", "2")
        wire = google_wire()
        connect(wire, "google")

        synced = {row["provider_message_id"] for row in workspace.rows()}
        assert synced == {"m-launch", "m-contract"}

    def test_the_arrival_time_is_stored_as_a_time(self, workspace, connect):
        """Gmail reports ``internalDate`` as epoch milliseconds in a string.
        Stored raw, the inbox renders '1758790860000' where the time should be,
        and sorting it against a Microsoft mailbox's ISO timestamps compares
        two different things."""
        wire = google_wire()
        connect(wire, "google")

        received = workspace.row("m-launch")["received_at"]
        assert not received.isdigit()
        assert datetime.fromisoformat(received).year == 2026


class TestWhatTheReaderIsGiven:
    """The stored body is not a display detail.

    It is what the reader shows, what the drafter is handed as "the message",
    and what the verifier checks a draft's claims against. Until the HTML
    extractor existed, for HTML-only mail all three of those were markup.
    """

    def test_an_html_only_message_is_stored_as_words(self, workspace, connect):
        wire = google_wire()
        connect(wire, "google")

        body = workspace.row("m-launch")["body"]
        assert "my board is asking for a date" in body
        assert "<p>" not in body
        assert "font-family" not in body

    def test_the_senders_own_plain_text_wins_over_the_html_alternative(self, workspace, connect):
        """When a sender provides both, the plain part is what they wrote,
        already wrapped the way they wrote it."""
        wire = google_wire()
        connect(wire, "google")

        body = workspace.row("m-notes")["body"]
        assert body.startswith("Alex — my notes from Tuesday")
        # The HTML alternative spells the dash and the apostrophe as entities;
        # arriving here decoded would mean the HTML path had been taken.
        assert "&mdash;" not in body
        assert "I'll send the consolidated summary on Friday." in body

    def test_a_style_block_is_not_part_of_the_message(self, workspace, connect):
        wire = google_wire()
        connect(wire, "google")

        body = workspace.row("m-status")["body"]
        assert "All systems nominal this week." in body
        assert "color:#f00" not in body

    def test_the_inbox_page_shows_the_words_and_not_the_markup(self, workspace, connect):
        wire = google_wire()
        connect(wire, "google")

        html = workspace.inbox(message=workspace.row("m-launch")["id"])

        assert "my board is asking for a date" in html
        # The message's own markup must not survive into the page — escaped or
        # otherwise. `font-family:Arial` appears in no stylesheet of ours.
        assert "font-family:Arial" not in html


class TestApprovingAReply:
    """The one irreversible thing the product does."""

    def _approve_the_reply(self, workspace, connect, content=None):
        wire = google_wire()
        connect(wire, "google")
        action = workspace.held("reply")
        response = workspace.approve(action["id"], content)
        assert response.status_code == 303
        return wire, action

    def test_the_reply_goes_where_the_sender_said_to_reply(self, workspace, connect):
        """Ticket systems and no-reply senders route replies away from From.
        Answering From anyway sends an approved reply somewhere nobody reads."""
        wire, _ = self._approve_the_reply(workspace, connect)

        assert len(wire.sent) == 1
        assert wire.sent[0].to == "tickets+dana@helpdesk.example"
        assert "dana.reyes@gmail.com" not in wire.sent[0].to

    def test_the_reply_threads_for_the_recipient(self, workspace, connect):
        """``threadId`` groups it on our side. The recipient's client threads on
        In-Reply-To/References, and without them every approved reply arrives as
        a brand-new conversation."""
        wire, _ = self._approve_the_reply(workspace, connect)

        sent = wire.sent[0]
        assert sent.threaded
        assert sent.in_reply_to == "<m-launch@mail.example>"
        # The whole chain travels, not just the last hop.
        assert "<earlier-note@mail.example>" in sent.references
        assert "<m-launch@mail.example>" in sent.references
        assert sent.thread_id == "thread-m-launch"

    def test_the_subject_reads_as_a_reply(self, workspace, connect):
        wire, _ = self._approve_the_reply(workspace, connect)

        assert wire.sent[0].subject == "Re: Urgent: launch date is slipping"

    def test_a_reviewers_edit_is_what_actually_gets_sent(self, workspace, connect):
        wire, action = self._approve_the_reply(
            workspace, connect, content="Thursday works. I will send the revised plan then."
        )

        assert wire.sent[0].body.strip().startswith("Thursday works.")
        stored = workspace.actions.get(workspace.org_id, action["id"])
        assert stored["outcome"] == "edited"
        assert stored["original_content"] == action["content"]

    def test_rejecting_sends_nothing(self, workspace, connect):
        wire = google_wire()
        connect(wire, "google")
        action = workspace.held("reply")

        assert workspace.reject(action["id"]).status_code == 303

        assert wire.sent == []
        assert workspace.actions.get(workspace.org_id, action["id"])["status"] == "rejected"

    def test_a_reply_the_workspace_sent_becomes_something_it_owes(self, workspace, connect):
        """The half of follow-up tracking only the sending party can see."""
        wire, _ = self._approve_the_reply(
            workspace, connect, content="I will send the revised plan on Thursday."
        )

        assert "I will send the revised plan on Thursday." in workspace.page("/app/waiting")


class TestEscalatingToAColleague:
    """An escalation names a role. Who holds that role is a fact about the
    workspace, and the artifact must never be addressed to the person who
    wrote in."""

    def test_the_handoff_goes_to_the_colleague(self, workspace, connect):
        wire = google_wire()
        connect(wire, "google")
        workspace.set_escalation_contact("legal_team", "counsel@northwind.example")

        action = workspace.held("escalate")
        assert workspace.approve(action["id"]).status_code == 303

        assert len(wire.drafts) == 1
        draft = wire.drafts[0]
        assert draft.to == "counsel@northwind.example"
        # Never the outside party who wrote in.
        assert "vendorlegal.example" not in draft.to
        assert draft.subject.startswith("Fwd:")
        # A forward, so the hand-off carries its own context...
        assert "liability cap in the signed contract" in draft.body
        # ...and it is not in the customer's own thread, where one stray Send
        # would tell them they are being escalated.
        assert draft.thread_id is None
        # A draft, not a send: nothing left the mailbox.
        assert wire.sent == []

    def test_an_unconfigured_role_fails_the_action_rather_than_guessing(self, workspace, connect):
        wire = google_wire()
        connect(wire, "google")

        action = workspace.held("escalate")
        assert workspace.approve(action["id"]).status_code == 303

        stored = workspace.actions.get(workspace.org_id, action["id"])
        assert stored["status"] == "failed"
        assert "no mailbox is configured" in stored["last_error"]
        assert wire.drafts == []
        assert wire.sent == []

    def test_filling_the_address_in_afterwards_honours_the_approval_already_given(
        self, workspace, connect
    ):
        """The human already decided. Once the address exists, the mechanical
        step is retried — the approval is not thrown away."""
        wire = google_wire()
        connect(wire, "google")
        action = workspace.held("escalate")
        workspace.approve(action["id"])
        assert workspace.actions.get(workspace.org_id, action["id"])["status"] == "failed"

        workspace.set_escalation_contact("legal_team", "counsel@northwind.example")
        summary = InboxSyncService().retry_failed_sends(org_id=workspace.org_id)

        assert summary == {"attempted": 1, "recovered": 1, "still_failing": 0}
        assert wire.drafts[0].to == "counsel@northwind.example"
        assert workspace.actions.get(workspace.org_id, action["id"])["status"] == "executed"


class TestFilingWhatItHandled:
    def test_a_label_is_created_once_and_applied_to_the_whole_group_in_one_call(
        self, workspace, connect
    ):
        """Gmail will not invent a label for you: it has to exist before it can
        be applied. ``batchModify`` then files a whole classification in one
        write instead of one per message — the difference between a 100-message
        first sync being ~200 round-trips and being 2."""
        wire = google_wire()
        connect(wire, "google")

        assert wire.created_labels == ["urgent", "normal", "spam", "deferred"]
        assert "urgent" in wire.label_names("m-launch")
        assert "deferred" in wire.label_names("m-status")
        assert "spam" in wire.label_names("m-deal")
        # urgent (2), normal (2) and deferred (2) batch; spam is a single
        # message, so it takes the per-message write.
        assert wire.call_count("POST", "batchModify") == 3
        assert wire.call_count("POST", "/modify") == 1

    def test_filing_a_message_keeps_the_labels_already_on_it(self, workspace, connect):
        """Gmail's addLabelIds is additive, and the INBOX/UNREAD state a person
        sees must survive being triaged."""
        wire = google_wire()
        connect(wire, "google")

        assert wire.label_names("m-launch") == ["INBOX", "UNREAD", "urgent"]

    def test_a_second_sync_proposes_nothing_and_writes_nothing(self, workspace, connect):
        """Re-syncing an inbox is normal — the worker does it every interval.
        It must not duplicate a proposal or re-fire a provider write."""
        wire = google_wire()
        connect(wire, "google")
        before = len(workspace.action_list())
        writes = wire.call_count("POST", "batchModify") + wire.call_count("POST", "/modify")

        assert workspace.sync_now().status_code == 303

        assert len(workspace.action_list()) == before
        assert wire.created_labels == ["urgent", "normal", "spam", "deferred"]
        assert wire.call_count("POST", "batchModify") + wire.call_count("POST", "/modify") == writes

    def test_archiving_takes_the_message_out_of_the_inbox(self, workspace, connect):
        """Gmail has no archive verb: it is ``removeLabelIds: ["INBOX"]``, and
        the only proof it worked is that the next list no longer returns it."""
        wire = google_wire()
        connection = connect(wire, "google")
        assert "m-status" in wire.in_inbox()

        assert build_provider(connection).archive("m-status").ok

        assert "m-status" not in wire.in_inbox()
        assert workspace.sync_now().status_code == 303
        assert wire.call_count("GET", "/messages?") == 2
        # The row stays — it was triaged. It is simply not read again.
        assert len(workspace.rows()) == 5


class TestFollowUpsFoundInRealMail:
    def test_a_request_in_an_html_body_reaches_the_waiting_list(self, workspace, connect):
        """The promise is inside the markup. Before the extractor, the text the
        commitment parser saw was a wall of tags."""
        wire = google_wire()
        connect(wire, "google")

        assert "Please confirm the revised timeline by Thursday." in workspace.page("/app/waiting")

    def test_a_promise_inside_spam_never_becomes_a_follow_up(self, workspace, connect):
        """ "Subscribe now and we will register your team at a permanent
        discount" parses as a perfectly good promise. Two of those in a
        seven-row list is how nobody opens the list again."""
        wire = google_wire()
        connect(wire, "google")

        assert "permanent discount" not in workspace.page("/app/waiting")
        texts = [
            row["text"]
            for row in workspace.commitments.list_for_org(workspace.org_id)["commitments"]
        ]
        assert not any("discount" in text for text in texts)
