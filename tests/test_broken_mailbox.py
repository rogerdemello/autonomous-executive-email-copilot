"""A mailbox that stops authenticating must be impossible to miss.

This is the failure mode that hurts most in an email product, because it
presents as nothing at all: the worker correctly stops retrying a broken
connection, the approval queue stops filling, and an inbox that has gone quiet
looks exactly like a quiet week. The connection's state was recorded correctly
the whole time — it was just only ever rendered as a chip on ``/app/connect``,
a page nobody opens twice.

Two properties: the state is visible everywhere a user actually works, and the
people who can fix it are told once — not once per sweep.
"""

from __future__ import annotations

import re
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.db import migrate_db
from app.main import app
from app.saas import provider_factory
from app.saas.email import MemorySender
from app.saas.repository import AuditRepository, MailboxRepository

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')
BANNER = "has stopped syncing and needs to be reconnected"


def csrf_from(html: str) -> str:
    match = CSRF_RE.search(html)
    assert match, "every page with a form must embed a CSRF token"
    return match.group(1)


@pytest.fixture(autouse=True)
def _schema():
    migrate_db()


@pytest.fixture
def client():
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def workspace(client):
    """A signed-in workspace with the demo mailbox connected."""
    email = f"owner_{uuid.uuid4().hex[:10]}@northwind.example"
    page = client.get("/signup").text
    assert (
        client.post(
            "/signup",
            data={
                "csrf_token": csrf_from(page),
                "org_name": "Northwind Industries",
                "full_name": "Alex Chen",
                "email": email,
                "password": "a-strong-password",
            },
        ).status_code
        == 303
    )
    page = client.get("/app/connect").text
    assert client.post("/app/connect/demo", data={"csrf_token": csrf_from(page)}).status_code == 303

    connections = _connections_of(email)
    return client, email, connections[0]


def _org_id_of(email: str) -> str:
    from app.saas.repository import UserRepository

    user = UserRepository().get_by_email_global(email)
    assert user, "the signup fixture must have created a user"
    return user["org_id"]


def _connections_of(email: str) -> list[dict]:
    return MailboxRepository().list_for_org(_org_id_of(email))


# --------------------------------------------------------------------------- #
# Visibility
# --------------------------------------------------------------------------- #
APP_PAGES = ["/app/inbox", "/app/approvals", "/app/waiting", "/app/activity", "/app/settings"]


class TestTheBanner:
    def test_a_healthy_workspace_shows_no_banner(self, workspace):
        client, _, _ = workspace
        for path in APP_PAGES:
            assert BANNER not in client.get(path).text, f"{path} cried wolf"

    @pytest.mark.parametrize("path", APP_PAGES)
    def test_a_broken_mailbox_is_flagged_on_every_page(self, workspace, path):
        client, email, connection = workspace
        MailboxRepository().set_status(_org_id_of(email), connection["id"], "error")

        response = client.get(path)

        assert response.status_code == 200
        assert BANNER in response.text
        assert "/app/connect" in response.text

    def test_the_connect_page_does_not_double_up(self, workspace):
        """It already shows a per-connection chip; a banner too is noise."""
        client, email, connection = workspace
        MailboxRepository().set_status(_org_id_of(email), connection["id"], "error")

        body = client.get("/app/connect").text

        assert BANNER not in body
        assert "needs reconnect" in body

    def test_one_broken_mailbox_flags_the_workspace(self, workspace):
        """A second healthy mailbox does not make the broken one acceptable."""
        client, email, connection = workspace
        org_id = _org_id_of(email)
        MailboxRepository().set_status(org_id, connection["id"], "error")
        assert MailboxRepository().any_broken(org_id)

        assert BANNER in client.get("/app/inbox").text


# --------------------------------------------------------------------------- #
# The notification
# --------------------------------------------------------------------------- #
class TestTheNotification:
    def _capture_mail(self, monkeypatch) -> MemorySender:
        """Patch the sender factory, which is resolved per send — no network."""
        sender = MemorySender()
        monkeypatch.setattr("app.saas.email.get_email_sender", lambda: sender)
        return sender

    def test_breaking_a_connection_emails_the_admins_once(self, workspace, monkeypatch):
        client, email, connection = workspace
        org_id = _org_id_of(email)
        sender = self._capture_mail(monkeypatch)
        full = dict(connection, org_id=org_id)

        provider_factory._mark_broken(full, "The token was revoked.")

        assert len(sender.outbox) == 1
        message = sender.outbox[0]
        assert message.to == email
        assert "stopped syncing" in message.subject
        assert "The token was revoked." in message.body
        assert "/app/connect" in message.body

    def test_a_connection_already_broken_does_not_re_notify(self, workspace, monkeypatch):
        """The worker re-derives this state every sweep — 96 mails a day is a filter rule."""
        client, email, connection = workspace
        org_id = _org_id_of(email)
        sender = self._capture_mail(monkeypatch)
        full = dict(connection, org_id=org_id)

        for _ in range(5):
            provider_factory._mark_broken(full, "The token was revoked.")

        assert len(sender.outbox) == 1

    def test_the_breakage_reaches_the_audit_log(self, workspace, monkeypatch):
        client, email, connection = workspace
        org_id = _org_id_of(email)
        self._capture_mail(monkeypatch)

        provider_factory._mark_broken(dict(connection, org_id=org_id), "Credentials unreadable.")

        entries = AuditRepository().list_for_org(org_id)
        broken = [row for row in entries if row["action"] == "mailbox.broken"]
        assert len(broken) == 1
        assert broken[0]["target"] == connection["id"]

    def test_a_failing_mailer_does_not_break_the_sync(self, workspace, monkeypatch):
        """Notification is best-effort; it may never be why a mailbox stops working."""
        client, email, connection = workspace
        org_id = _org_id_of(email)

        def _explode():
            raise RuntimeError("SMTP is down")

        monkeypatch.setattr("app.saas.email.get_email_sender", _explode)

        error = provider_factory._mark_broken(dict(connection, org_id=org_id), "Token revoked.")

        assert isinstance(error, provider_factory.BrokenConnectionError)
        assert MailboxRepository().any_broken(org_id)


# --------------------------------------------------------------------------- #
# The transition signal the notification depends on
# --------------------------------------------------------------------------- #
class TestSetStatusReportsTransitions:
    def test_a_real_change_reports_true(self, workspace):
        _, email, connection = workspace
        assert MailboxRepository().set_status(_org_id_of(email), connection["id"], "error")

    def test_setting_the_same_status_again_reports_false(self, workspace):
        _, email, connection = workspace
        org_id = _org_id_of(email)
        MailboxRepository().set_status(org_id, connection["id"], "error")
        assert not MailboxRepository().set_status(org_id, connection["id"], "error")

    def test_an_unknown_connection_reports_false(self, workspace):
        _, email, _ = workspace
        assert not MailboxRepository().set_status(_org_id_of(email), "nope", "error")

    def test_reconnecting_clears_the_flag(self, workspace):
        _, email, connection = workspace
        org_id = _org_id_of(email)
        MailboxRepository().set_status(org_id, connection["id"], "error")
        assert MailboxRepository().any_broken(org_id)

        assert MailboxRepository().set_status(org_id, connection["id"], "connected")
        assert not MailboxRepository().any_broken(org_id)
