"""A real Microsoft 365 account, end to end, through the product.

Same shape as the Gmail file, and the differences are the point: Graph returns
whole messages in the list response, its bodies are HTML *by default*, its
nearest thing to a label is a collection that a PATCH replaces wholesale, and it
publishes no default sort order for a mailbox. Every one of those four is a bug
this product shipped and the demo mailbox could not have caught.
"""

from __future__ import annotations

from app.saas.provider_factory import build_provider

from . import wire as w
from .conftest import microsoft_wire


class TestReadingTheMailbox:
    def test_the_whole_mailbox_arrives_in_one_call(self, workspace, connect):
        """Graph returns full message resources in the list response, so unlike
        Gmail there is no per-message body fetch. The only per-message reads in
        a sync are the category lookups a label write needs."""
        wire = microsoft_wire()
        connect(wire, "microsoft")

        assert wire.call_count("GET", "/mailFolders/inbox/messages") == 1
        body_reads = [
            url
            for method, url in wire.calls
            if method == "GET" and "/messages/" in url and "$select=id,categories" not in url
        ]
        assert body_reads == []

    def test_the_newest_mail_is_asked_for_explicitly(self, workspace, connect, monkeypatch):
        """Graph does not document a default order for ``/messages``, so the
        fake answers an ``$orderby``-less request with the oldest mail first.
        Triage is about what just arrived, and a sweep is capped — so a provider
        that leaves the order to the service syncs a mailbox's history forever
        and never reaches the message somebody is waiting on."""
        monkeypatch.setenv("INBOX_SYNC_LIMIT", "2")
        wire = microsoft_wire()
        connect(wire, "microsoft")

        synced = {row["provider_message_id"] for row in workspace.rows()}
        assert synced == {"m-launch", "m-contract"}

    def test_the_inbox_lists_the_newest_first(self, workspace, connect):
        wire = microsoft_wire()
        connect(wire, "microsoft")

        order = [row["provider_message_id"] for row in workspace.rows()]
        assert order == ["m-launch", "m-contract", "m-notes", "m-status", "m-deal"]


class TestWhatTheReaderIsGiven:
    """``body.contentType`` is html by default, so taking ``body.content`` as it
    comes stored markup for essentially every Outlook message — and that markup
    became the reader's text, the drafter's "message", and the verifier's source.
    """

    def test_an_outlook_body_is_stored_as_words(self, workspace, connect):
        wire = microsoft_wire()
        connect(wire, "microsoft")

        body = workspace.row("m-launch")["body"]
        assert "my board is asking for a date" in body
        assert "<p>" not in body
        assert 'dir="ltr"' not in body

    def test_html_a_sender_composed_into_a_text_message_is_still_rendered(self, workspace, connect):
        """The declared type is trusted when it says text — and still sniffed.
        A sender can compose HTML into a message Graph labels ``text``, and the
        cost of believing the label is a wall of tags in the reader."""
        wire = microsoft_wire(
            [
                w.message(
                    id="m-mislabelled",
                    sender="ops@vendor.example",
                    subject="Urgent: please confirm receipt",
                    received_at="2026-09-25T07:00:00+00:00",
                    html="<div><p>Signed and returned.</p></div>",
                    graph_declares="text",
                )
            ]
        )
        connect(wire, "microsoft")

        body = workspace.row("m-mislabelled")["body"]
        assert body == "Signed and returned."

    def test_an_empty_body_falls_back_to_the_preview(self, workspace, connect):
        wire = microsoft_wire(
            [
                w.message(
                    id="m-empty",
                    sender="chair@board.example",
                    subject="Urgent: call me",
                    received_at="2026-09-25T07:30:00+00:00",
                    snippet="Ring me when you land.",
                )
            ]
        )
        connect(wire, "microsoft")

        assert workspace.row("m-empty")["body"] == "Ring me when you land."


class TestFilingWithoutDestroyingWhatWasThere:
    """Graph has no labels. The nearest concept is a category — and
    ``categories`` is a collection, so a PATCH *replaces* it. Sending only ours
    deleted every category the person had filed the message under, in their own
    mailbox, as a side effect of triage."""

    def test_the_categories_a_person_filed_it_under_survive_triage(self, workspace, connect):
        wire = microsoft_wire()
        connect(wire, "microsoft")

        assert wire.messages["m-launch"].categories == ["Clients", "urgent"]
        assert wire.messages["m-contract"].categories == ["Legal hold", "urgent"]

    def test_two_filings_in_one_sweep_both_survive(self, workspace, connect):
        """A message classified and then downgraded to ``deferred`` in the same
        sweep used to keep whichever write happened to land second."""
        wire = microsoft_wire()
        connect(wire, "microsoft")

        assert wire.messages["m-notes"].categories == ["normal", "deferred"]

    def test_filing_a_message_where_it_already_is_writes_nothing(self, workspace, connect):
        """Re-PATCHing identical values bumps the message's changeKey and marks
        the mailbox changed for nothing."""
        wire = microsoft_wire(
            [
                w.message(
                    id="m-already",
                    sender="dana.reyes@gmail.com",
                    subject="Urgent: one more thing",
                    received_at="2026-09-25T07:45:00+00:00",
                    text="Urgent: can you confirm the date.",
                    categories=("urgent",),
                )
            ]
        )
        connect(wire, "microsoft")

        assert wire.category_patches == []
        assert wire.messages["m-already"].categories == ["urgent"]


class TestApprovingAReply:
    def test_a_reply_graph_accepts_without_a_body_still_counts_as_sent(self, workspace, connect):
        """``/reply`` answers 202 with no content at all. The transport has to
        read that as success, or every approved Outlook reply lands in
        ``failed`` after having actually been sent."""
        wire = microsoft_wire()
        connect(wire, "microsoft")
        action = workspace.held("reply")

        assert workspace.approve(action["id"]).status_code == 303

        assert len(wire.sent) == 1
        assert wire.sent[0].subject == "Re: Urgent: launch date is slipping"
        assert workspace.actions.get(workspace.org_id, action["id"])["status"] == "executed"

    def test_graph_threads_and_addresses_the_reply_itself(self, workspace, connect):
        """Unlike Gmail, the reply's addressing and threading are Graph's job —
        including Reply-To, which is why this provider never reads it."""
        wire = microsoft_wire()
        connect(wire, "microsoft")
        workspace.approve(workspace.held("reply")["id"])

        sent = wire.sent[0]
        assert sent.to == "tickets+dana@helpdesk.example"
        assert sent.threaded
        assert sent.thread_id == "thread-m-launch"


class TestEscalatingToAColleague:
    def test_the_handoff_is_a_forward_to_the_colleague(self, workspace, connect):
        """``createForward`` is exactly this operation, and Graph attaches the
        original itself. It replaced ``createReply``, which addressed an
        internal hand-off to the outside party who wrote in."""
        wire = microsoft_wire()
        connect(wire, "microsoft")
        workspace.set_escalation_contact("legal_team", "counsel@northwind.example")

        assert workspace.approve(workspace.held("escalate")["id"]).status_code == 303

        assert len(wire.drafts) == 1
        draft = wire.drafts[0]
        assert draft.to == "counsel@northwind.example"
        assert "vendorlegal.example" not in draft.to
        assert "Escalating to legal team" in draft.body
        assert "liability cap in the signed contract" in draft.body
        assert draft.thread_id is None
        assert wire.sent == []

    def test_an_unconfigured_role_fails_the_action_rather_than_guessing(self, workspace, connect):
        wire = microsoft_wire()
        connect(wire, "microsoft")

        action = workspace.held("escalate")
        workspace.approve(action["id"])

        stored = workspace.actions.get(workspace.org_id, action["id"])
        assert stored["status"] == "failed"
        assert "no mailbox is configured" in stored["last_error"]
        assert wire.drafts == []


class TestArchiving:
    def test_an_archived_message_leaves_the_inbox_and_stays_out(self, workspace, connect):
        """``/move`` is the only write whose effect is invisible in the response:
        the proof is that the next fetch no longer returns it."""
        wire = microsoft_wire()
        connection = connect(wire, "microsoft")
        assert "m-status" in wire.in_inbox()

        result = build_provider(connection).archive("m-status")

        assert result.ok
        assert "m-status" not in wire.in_inbox()
        assert workspace.sync_now().status_code == 303
        # Already-synced rows stay; the message is simply not read again.
        assert wire.call_count("GET", "/mailFolders/inbox/messages") == 2
        assert len(workspace.rows()) == 5

    def test_a_folder_graph_does_not_know_is_a_failed_write_not_a_crash(self, workspace, connect):
        wire = microsoft_wire()
        connection = connect(wire, "microsoft")

        provider = build_provider(connection)
        result = provider.add_label("not-a-real-message-id", "urgent")

        assert result.ok is False
        assert result.detail
