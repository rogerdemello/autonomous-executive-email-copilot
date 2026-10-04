"""One workspace, a Gmail account and a Microsoft 365 account.

This is the configuration ``LAUNCH_CHECKLIST.md`` asks you to put a real
customer into, and the two properties it needs are the ones no single-provider
test can state: identical mail must be routed identically whichever provider it
arrived through, and the two mailboxes must not share fate.
"""

from __future__ import annotations

from app.saas.email import MemorySender
from app.saas.sync_worker import BackgroundSyncWorker

from . import wire as w
from .conftest import google_wire, microsoft_wire, pin_sweep_to_workspace


def _chaser(message_id: str) -> w.WireMessage:
    """New mail, so a sweep has something to show for itself."""
    return w.message(
        id=message_id,
        sender="dana.reyes@gmail.com",
        sender_name="Dana Reyes",
        subject="Urgent: still waiting on that date",
        received_at="2026-09-26T08:00:00+00:00",
        text="Urgent — the board meets tomorrow. Please confirm today.",
    )


def _decisions(workspace, connection_id: str) -> dict[str, list[str]]:
    """``{provider_message_id: [action types]}`` for one connection."""
    rows = {
        row["id"]: row["provider_message_id"]
        for row in workspace.messages.list_for_org(workspace.org_id, connection_id=connection_id)[
            "messages"
        ]
    }
    decisions: dict[str, list[str]] = {name: [] for name in rows.values()}
    for action in workspace.action_list():
        provider_message_id = rows.get(action["message_id"])
        if provider_message_id is None:
            continue
        label = action["label"]
        decisions[provider_message_id].append(
            f"{action['action_type']}:{label}" if label else action["action_type"]
        )
    return {key: sorted(value) for key, value in decisions.items()}


class TestTheTwoProvidersAgree:
    def test_the_same_mail_is_routed_the_same_way_through_either_provider(self, workspace, connect):
        """The routing is computed from the message text, so a Gmail account and
        an Outlook account holding the same mail must reach the same decisions.
        They did not, for a while, and the reason was the body: Graph's arrived
        as markup, which changes what the classifier reads."""
        gmail = google_wire()
        graph = microsoft_wire()
        google_connection = connect(gmail, "google")
        microsoft_connection = connect(graph, "microsoft")

        through_gmail = _decisions(workspace, google_connection["id"])
        through_graph = _decisions(workspace, microsoft_connection["id"])

        assert through_gmail == through_graph
        # Not vacuously: the five messages produce four distinct outcomes.
        assert through_gmail["m-launch"] == ["classify:urgent", "reply"]
        assert through_gmail["m-contract"] == ["classify:urgent", "escalate"]
        assert through_gmail["m-notes"] == ["classify:normal", "defer:deferred"]
        assert through_gmail["m-deal"] == ["classify:spam"]

    def test_both_mailboxes_read_the_same_body(self, workspace, connect):
        gmail = google_wire()
        graph = microsoft_wire()
        connect(gmail, "google")
        connect(graph, "microsoft")

        bodies = {
            row["body"] for row in workspace.rows() if row["provider_message_id"] == "m-launch"
        }
        assert len(bodies) == 1, "the same HTML message read two different ways"


class TestTheyDoNotShareFate:
    def test_a_sweep_works_both_mailboxes(self, workspace, connect, monkeypatch):
        gmail = google_wire()
        graph = microsoft_wire()
        connect(gmail, "google")
        connect(graph, "microsoft")
        pin_sweep_to_workspace(monkeypatch, workspace)

        gmail.deliver(_chaser("m-gmail-chase"))
        graph.deliver(_chaser("m-graph-chase"))
        summary = BackgroundSyncWorker(interval_seconds=0).sync_due_connections()

        assert summary["checked"] == 2
        assert summary["synced"] == 2
        synced = {row["provider_message_id"] for row in workspace.rows()}
        assert {"m-gmail-chase", "m-graph-chase"} <= synced

    def test_a_revoked_gmail_does_not_stop_the_microsoft_mailbox(
        self, workspace, connect, monkeypatch
    ):
        """The worker's first constraint: one broken mailbox is logged and backed
        off, and every other connection still syncs on schedule."""
        gmail = google_wire()
        graph = microsoft_wire()
        connect(gmail, "google")
        connect(graph, "microsoft")
        monkeypatch.setattr("app.saas.email.get_email_sender", lambda: MemorySender())
        pin_sweep_to_workspace(monkeypatch, workspace)

        gmail.revoke_refresh_token()
        graph.deliver(_chaser("m-graph-chase"))
        summary = BackgroundSyncWorker(interval_seconds=0).sync_due_connections()

        assert summary["errors"] == 1
        assert summary["synced"] == 1
        assert workspace.row("m-graph-chase")
        assert workspace.connection("google")["status"] == "error"
        assert workspace.connection("microsoft")["status"] == "connected"
