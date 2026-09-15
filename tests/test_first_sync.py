"""The first minutes of a real customer's account.

Connecting a mailbox used to sync it *inside the OAuth redirect handler*. The
demo mailbox hid that completely — ``DemoProvider`` is in memory and its drafts
are cached, so the call returned instantly. A real mailbox is not that: at the
default ``inbox_sync_limit`` of 100, Gmail alone is one list call plus a hundred
sequential message fetches, and with drafting on every held action costs two
more model calls. Minutes of work, in a redirect, with nothing bounding it — so
the customer's first act after granting consent returned a proxy timeout.

Two properties here. The callback returns without doing the work, and the
customer is told the mailbox is being read rather than being told it is empty.
"""

from __future__ import annotations

import re
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.db import migrate_db
from app.main import app
from app.saas import oauth
from app.saas.repository import MailboxRepository, UserRepository

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


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
def signed_in(client):
    """A signed-in workspace with no mailbox attached yet."""
    email = f"first_{uuid.uuid4().hex[:10]}@northwind.example"
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
    return client, email


def _org_id(email: str) -> str:
    user = UserRepository().get_by_email_global(email)
    assert user
    return user["org_id"]


# --------------------------------------------------------------------------- #
# The callback must not do the work
# --------------------------------------------------------------------------- #
class TestTheCallbackReturnsImmediately:
    def _connect(self, client, monkeypatch, email):
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csecret")
        monkeypatch.setattr(
            oauth,
            "exchange_code",
            lambda provider, *, code, client_id, client_secret, redirect: {
                "access_token": "a",
                "refresh_token": "r",
                "expires_in": 3600,
                "id_token": "",
            },
        )
        state = oauth.sign_state(org_id=_org_id(email), user_id="u-1", provider="google")
        return client.get("/mailbox/oauth/callback", params={"code": "c", "state": state})

    def test_the_handler_defers_the_sync_rather_than_running_it(self):
        """Starlette only defers work when the handler takes ``BackgroundTasks``.

        Checked structurally because the behaviour itself is not observable
        here: ``TestClient`` runs background tasks before it hands the response
        back, so from the outside a deferred sync and an inline one look
        identical. The thing that makes them different in production is this
        parameter, so this is what there is to assert.
        """
        import inspect
        import typing

        from fastapi import BackgroundTasks

        from app.saas.mailbox_routes import oauth_callback

        # get_type_hints, not the raw signature: the module uses
        # `from __future__ import annotations`, so annotations are strings.
        hints = typing.get_type_hints(oauth_callback)
        assert BackgroundTasks in hints.values(), (
            "oauth_callback must take BackgroundTasks, or the first sync runs "
            "inside the redirect and a real mailbox times out the proxy"
        )
        source = inspect.getsource(oauth_callback)
        assert "add_task" in source
        assert "InboxSyncService" not in source

    def test_the_sync_is_scheduled_as_a_background_task(self, signed_in, monkeypatch):
        """It still has to happen — just not before the redirect."""
        client, email = signed_in
        calls = []

        import app.saas.sync_service as sync_service

        monkeypatch.setattr(
            sync_service.InboxSyncService,
            "sync",
            lambda self, **kwargs: calls.append(kwargs) or {"messages": 0, "proposed": 0},
        )

        self._connect(client, monkeypatch, email)

        # TestClient flushes background tasks before returning.
        assert len(calls) == 1
        assert calls[0]["org_id"] == _org_id(email)

    def test_a_failing_first_sync_does_not_break_the_connection(self, signed_in, monkeypatch):
        """The connection worked; the pull is the worker's problem now."""
        client, email = signed_in

        import app.saas.sync_service as sync_service

        def _explode(self, **kwargs):
            raise RuntimeError("provider is down")

        monkeypatch.setattr(sync_service.InboxSyncService, "sync", _explode)

        response = self._connect(client, monkeypatch, email)

        assert response.status_code == 303
        connections = MailboxRepository().list_for_org(_org_id(email))
        assert len(connections) == 1
        # Never synced, so BackgroundSyncWorker.is_due() picks it straight up.
        assert not connections[0]["last_synced_at"]


# --------------------------------------------------------------------------- #
# What the customer sees while it runs
# --------------------------------------------------------------------------- #
class TestTheWaitingState:
    def _attach_unsynced_mailbox(self, email: str) -> None:
        MailboxRepository().upsert_connection(
            org_id=_org_id(email),
            provider="google",
            account_email="alex@northwind.example",
            connected_by=None,
            access_token_enc=None,
            refresh_token_enc=None,
            token_expires_at=None,
            scopes=None,
        )

    def test_a_connected_unsynced_mailbox_says_it_is_being_read(self, signed_in):
        """It used to say 'nothing matches that filter' — telling a customer
        their inbox is empty when the copilot had not finished reading it."""
        client, email = signed_in
        self._attach_unsynced_mailbox(email)

        html = client.get("/app/inbox").text

        assert "Reading your mailbox" in html
        assert "Nothing matches that filter" not in html
        assert "Nothing to triage" not in html

    def test_the_waiting_state_refreshes_itself(self, signed_in):
        """Server-rendered, so a meta refresh rather than a poll — the one
        screen a brand-new customer stares at should not need scripting."""
        client, email = signed_in
        self._attach_unsynced_mailbox(email)

        html = client.get("/app/inbox").text

        assert 'http-equiv="refresh"' in html

    def test_no_mailbox_at_all_still_says_connect_one(self, signed_in):
        client, _ = signed_in

        html = client.get("/app/inbox").text

        assert "No mailbox connected yet" in html
        assert "Reading your mailbox" not in html
        assert 'http-equiv="refresh"' not in html

    def test_a_synced_empty_mailbox_does_not_claim_to_be_working(self, signed_in):
        """Synced and genuinely empty is a different, calmer message — and it
        must not keep reloading the page forever."""
        client, email = signed_in
        self._attach_unsynced_mailbox(email)
        org_id = _org_id(email)
        connection = MailboxRepository().list_for_org(org_id)[0]
        MailboxRepository().set_synced_at(org_id, connection["id"], "2026-09-15T09:00:00+00:00")

        html = client.get("/app/inbox").text

        assert "Nothing to triage" in html
        assert "Reading your mailbox" not in html
        assert 'http-equiv="refresh"' not in html

    def test_a_populated_inbox_never_shows_the_waiting_state(self, signed_in):
        """The demo path syncs inline, so it lands on real messages."""
        client, _ = signed_in
        page = client.get("/app/connect").text
        assert (
            client.post("/app/connect/demo", data={"csrf_token": csrf_from(page)}).status_code
            == 303
        )

        html = client.get("/app/inbox").text

        assert "Reading your mailbox" not in html
        assert 'http-equiv="refresh"' not in html


# --------------------------------------------------------------------------- #
# The admin-consent wall
# --------------------------------------------------------------------------- #
class TestAdminConsent:
    """`Mail.ReadWrite`/`Mail.Send` need tenant-admin approval in most managed
    directories, so for a corporate customer this is the *expected* first
    outcome of clicking Connect — not an edge case. Microsoft reports it as
    `access_denied`, which is indistinguishable from the user saying no, so
    without this the onboarding call ends on "authorization was denied" and
    nobody knows which permission to chase.
    """

    @pytest.fixture
    def microsoft(self, monkeypatch):
        monkeypatch.setenv("MICROSOFT_OAUTH_CLIENT_ID", "ms-client-id")
        monkeypatch.setenv("MICROSOFT_OAUTH_CLIENT_SECRET", "ms-secret")

    def test_a_consent_wall_shows_the_administrator_link(self, client, microsoft):
        response = client.get(
            "/mailbox/oauth/callback",
            params={
                "error": "access_denied",
                "error_description": (
                    "AADSTS65001: The user or administrator has not consented to use "
                    "the application with ID '...'."
                ),
            },
        )

        assert response.status_code == 200
        body = response.text
        assert "administrator" in body.lower()
        assert "login.microsoftonline.com" in body
        assert "adminconsent" in body
        assert "ms-client-id" in body
        # It must not read as the customer's mistake.
        assert "Authorization was denied" not in body

    def test_an_ordinary_refusal_is_still_an_ordinary_error(self, client, microsoft):
        """Someone who genuinely clicked Cancel should not be sent to IT."""
        response = client.get(
            "/mailbox/oauth/callback",
            params={"error": "access_denied", "error_description": "User cancelled the flow"},
        )

        assert response.status_code == 400
        assert "adminconsent" not in response.text

    def test_the_consent_url_avoids_common_which_has_no_administrator(self, monkeypatch, microsoft):
        """`common` also covers personal Microsoft accounts, which have no
        admin and cannot grant this."""
        monkeypatch.setenv("MICROSOFT_OAUTH_TENANT", "common")
        url = oauth.admin_consent_url("https://app.example/mailbox/oauth/callback")
        assert "/organizations/adminconsent" in url

    def test_a_pinned_tenant_is_used_as_given(self, monkeypatch, microsoft):
        monkeypatch.setenv("MICROSOFT_OAUTH_TENANT", "contoso.onmicrosoft.com")
        url = oauth.admin_consent_url("https://app.example/mailbox/oauth/callback")
        assert "/contoso.onmicrosoft.com/adminconsent" in url

    def test_no_client_id_means_no_link_rather_than_a_broken_one(self, monkeypatch):
        monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_ID", raising=False)
        monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_SECRET", raising=False)
        assert oauth.admin_consent_url("https://app.example/cb") is None

    @pytest.mark.parametrize(
        "description",
        [
            "AADSTS65001: The user or administrator has not consented",
            "AADSTS900941: admin consent is required",
            "consent_required",
        ],
    )
    def test_the_known_consent_codes_are_recognised(self, description):
        assert oauth.needs_admin_consent("access_denied", description)

    def test_an_unrelated_failure_is_not_mistaken_for_a_consent_wall(self):
        assert not oauth.needs_admin_consent("access_denied", "AADSTS50011: redirect mismatch")
        assert not oauth.needs_admin_consent("server_error", None)
        assert not oauth.needs_admin_consent(None, None)


# --------------------------------------------------------------------------- #
# What the connect page promises
# --------------------------------------------------------------------------- #
class TestConnectPageCopy:
    def test_it_does_not_claim_read_only_access(self, signed_in):
        """It said "read-only access" while requesting gmail.modify, which
        writes labels. Untrue to the customer, and the sort of claim that fails
        an OAuth review for contradicting the scope list beside it."""
        client, _ = signed_in

        html = client.get("/app/connect").text

        assert "read-only" not in html.lower()

    def test_a_configured_provider_describes_what_it_will_actually_do(
        self, signed_in, monkeypatch
    ):
        """The copy that matters only renders once a provider is switched on,
        which is the state a real customer sees and the tests never were."""
        client, _ = signed_in
        monkeypatch.setenv("MICROSOFT_OAUTH_CLIENT_ID", "cid")
        monkeypatch.setenv("MICROSOFT_OAUTH_CLIENT_SECRET", "secret")

        html = client.get("/app/connect").text

        assert "files what it handles with a label" in html
        # Jinja escapes the apostrophe; assert on the part that has none.
        assert "never sends anything you" in html
        assert "read-only" not in html.lower()

    def test_an_unavailable_provider_addresses_the_customer_not_the_operator(self, signed_in):
        """On a self-serve signup the person reading this can never be the
        person who would fix it."""
        client, _ = signed_in

        html = client.get("/app/connect").text

        assert "An operator must set" not in html
        assert "client id and secret" not in html
        assert "isn't switched on for this workspace yet" in html
