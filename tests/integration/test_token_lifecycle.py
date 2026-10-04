"""What happens to a connected mailbox over time.

An access token lasts an hour. A refresh token lasts until the customer removes
the app, an administrator revokes it, or — on a Google consent screen still in
"Testing" — seven days. So every long-lived mailbox goes through both events, and
both of them happen *inside a provider call*, not at connect time.

That is why these are integration tests and cannot be anything else: the refresh
is wired in one module (``provider_factory``), triggered in another (the
provider's 401 retry), persisted through a third (the token vault and the
repository), and the proof that it worked is that the *next* sync succeeds
without refreshing again.
"""

from __future__ import annotations

from app.saas.crypto import get_vault
from app.saas.email import MemorySender
from app.saas.repository import AuditRepository, MailboxRepository
from app.saas.sync_worker import BackgroundSyncWorker

from . import wire as w
from .conftest import google_wire, microsoft_wire, pin_sweep_to_workspace

BANNER = "has stopped syncing and needs to be reconnected"


def _new_mail() -> w.WireMessage:
    return w.message(
        id="m-followup",
        sender="dana.reyes@gmail.com",
        sender_name="Dana Reyes",
        subject="Urgent: any word on the date?",
        received_at="2026-09-26T07:10:00+00:00",
        text="Chasing this — urgent for my board call. Please confirm today.",
    )


def _stored(org_id: str, connection_id: str) -> dict:
    full = MailboxRepository().get_with_tokens(org_id, connection_id)
    assert full
    return full


def _capture_mail(monkeypatch) -> MemorySender:
    sender = MemorySender()
    monkeypatch.setattr("app.saas.email.get_email_sender", lambda: sender)
    return sender


class TestAnExpiredAccessToken:
    """The hourly event. It must be invisible to the customer."""

    def test_the_sync_refreshes_itself_and_finishes_the_work(self, workspace, connect):
        wire = google_wire()
        connect(wire, "google")
        wire.expire_access_tokens()
        wire.deliver(_new_mail())

        assert workspace.sync_now().status_code == 303

        assert wire.refresh_calls == 1
        assert workspace.row("m-followup")["subject"] == "Urgent: any word on the date?"

    def test_the_refreshed_token_is_the_one_that_gets_stored(self, workspace, connect):
        """Re-encrypted and persisted. If it were not, every provider call for
        the rest of the mailbox's life would cost an extra round-trip and a
        refresh — and the refresh endpoint is rate-limited."""
        wire = google_wire()
        connection = connect(wire, "google")
        wire.expire_access_tokens()
        workspace.sync_now()

        stored = _stored(workspace.org_id, connection["id"])
        assert get_vault().decrypt(stored["access_token_enc"]) == wire.auth.minted_access_tokens[-1]

    def test_the_next_sync_uses_it_instead_of_refreshing_again(self, workspace, connect):
        wire = google_wire()
        connect(wire, "google")
        wire.expire_access_tokens()
        workspace.sync_now()

        wire.deliver(_new_mail())
        assert workspace.sync_now().status_code == 303

        assert wire.refresh_calls == 1

    def test_google_keeps_the_refresh_token_it_was_given(self, workspace, connect):
        """Google does not return a new refresh token on refresh, and the
        product must keep the one it has rather than storing nothing."""
        wire = google_wire()
        connection = connect(wire, "google")
        before = get_vault().decrypt(
            _stored(workspace.org_id, connection["id"])["refresh_token_enc"]
        )
        wire.expire_access_tokens()
        workspace.sync_now()

        after = get_vault().decrypt(
            _stored(workspace.org_id, connection["id"])["refresh_token_enc"]
        )
        assert after == before


class TestMicrosoftRotatesItsRefreshToken:
    """Entra returns a *new* refresh token every time one is used, and the old
    one stops working. Storing the wrong one is a mailbox that syncs exactly
    once more and then dies — a full interval later, with nothing to point at.
    """

    def test_the_rotated_token_is_stored_and_then_used(self, workspace, connect):
        wire = microsoft_wire()
        connection = connect(wire, "microsoft")

        wire.expire_access_tokens()
        assert workspace.sync_now().status_code == 303
        rotated = get_vault().decrypt(
            _stored(workspace.org_id, connection["id"])["refresh_token_enc"]
        )
        assert rotated == wire.auth.refresh_token

        # The second refresh is the assertion: the fake rejects the superseded
        # token, so this only passes if the rotated one was persisted.
        wire.expire_access_tokens()
        wire.deliver(_new_mail())
        assert workspace.sync_now().status_code == 303

        assert wire.refresh_calls == 2
        assert workspace.row("m-followup")


class TestARevokedMailbox:
    """The failure that hurts most, because it presents as nothing at all: the
    queue stops filling, and an inbox that has gone quiet looks exactly like a
    quiet week. The state has to be recorded, the page has to say so, and the
    people who can fix it have to be told — once.
    """

    def test_a_revoked_refresh_token_flags_the_mailbox(self, workspace, connect, monkeypatch):
        _capture_mail(monkeypatch)
        wire = google_wire()
        connection = connect(wire, "google")
        wire.revoke_refresh_token()

        response = workspace.sync_now()

        # Not a 500: the mailbox needs a human, and the app knows how to say so.
        assert response.status_code == 409
        assert MailboxRepository().get(workspace.org_id, connection["id"])["status"] == "error"

    def test_every_signed_in_page_says_so(self, workspace, connect, monkeypatch):
        _capture_mail(monkeypatch)
        wire = google_wire()
        connect(wire, "google")
        wire.revoke_refresh_token()
        workspace.sync_now()

        for path in ("/app/inbox", "/app/approvals", "/app/waiting", "/app/settings"):
            assert BANNER in workspace.page(path), f"{path} said nothing"

    def test_the_admins_are_emailed_once(self, workspace, connect, monkeypatch):
        sender = _capture_mail(monkeypatch)
        wire = google_wire()
        connect(wire, "google")
        wire.revoke_refresh_token()

        workspace.sync_now()
        workspace.sync_now()
        workspace.sync_now()

        assert len(sender.outbox) == 1
        assert sender.outbox[0].to == workspace.email
        assert "stopped syncing" in sender.outbox[0].subject

    def test_the_breakage_reaches_the_audit_log(self, workspace, connect, monkeypatch):
        _capture_mail(monkeypatch)
        wire = google_wire()
        connection = connect(wire, "google")
        wire.revoke_refresh_token()
        workspace.sync_now()

        broken = [
            row
            for row in AuditRepository().list_for_org(workspace.org_id)
            if row["action"] == "mailbox.broken"
        ]
        assert len(broken) == 1
        assert broken[0]["target"] == connection["id"]

    def test_the_background_worker_flags_it_rather_than_crashing(
        self, workspace, connect, monkeypatch
    ):
        """Nobody presses Sync. The worker is what actually discovers this, and
        it has to come out of the pass with the mailbox marked rather than with
        a stack trace and a connection it will retry forever."""
        _capture_mail(monkeypatch)
        wire = google_wire()
        connection = connect(wire, "google")
        wire.revoke_refresh_token()
        pin_sweep_to_workspace(monkeypatch, workspace)

        worker = BackgroundSyncWorker(interval_seconds=0)
        first = worker.sync_due_connections()

        assert first["checked"] == 1
        assert first["synced"] == 0
        assert first["errors"] == 1
        assert MailboxRepository().get(workspace.org_id, connection["id"])["status"] == "error"

    def test_the_worker_then_leaves_it_alone(self, workspace, connect, monkeypatch):
        """A broken connection needs a human, not retries — and it must not keep
        the rest of the sweep from happening."""
        _capture_mail(monkeypatch)
        wire = google_wire()
        connect(wire, "google")
        wire.revoke_refresh_token()
        pin_sweep_to_workspace(monkeypatch, workspace)

        worker = BackgroundSyncWorker(interval_seconds=0)
        worker.sync_due_connections()
        second = worker.sync_due_connections()

        assert second["checked"] == 0
        assert second["errors"] == 0


class TestAProviderHavingABadMinute:
    """The other half of flagging a mailbox: not flagging one that is fine.

    A refusal (``invalid_grant``, 4xx) is the end of the mailbox. An unreachable
    token endpoint, or a 5xx from it, is thirty seconds of somebody else's
    outage — and "reconnect your mailbox", emailed to a customer whose mailbox is
    perfectly good, is how a real alert becomes one people ignore.
    """

    def test_a_5xx_from_the_token_endpoint_does_not_flag_the_mailbox(
        self, workspace, connect, monkeypatch
    ):
        sender = _capture_mail(monkeypatch)
        wire = google_wire()
        connection = connect(wire, "google")
        wire.break_the_token_endpoint()
        pin_sweep_to_workspace(monkeypatch, workspace)

        summary = BackgroundSyncWorker(interval_seconds=0).sync_due_connections()

        assert summary["errors"] == 1
        assert MailboxRepository().get(workspace.org_id, connection["id"])["status"] == "connected"
        assert sender.outbox == []

    def test_and_it_is_still_swept_once_the_provider_recovers(
        self, workspace, connect, monkeypatch
    ):
        """The point of not flagging it: the worker picks it up again by itself."""
        _capture_mail(monkeypatch)
        wire = google_wire()
        connect(wire, "google")
        wire.break_the_token_endpoint()
        pin_sweep_to_workspace(monkeypatch, workspace)
        worker = BackgroundSyncWorker(interval_seconds=0)
        worker.sync_due_connections()

        wire.auth.refresh_unavailable = False
        wire.deliver(_new_mail())
        recovered = worker.sync_due_connections()

        assert recovered["synced"] == 1
        assert workspace.row("m-followup")
