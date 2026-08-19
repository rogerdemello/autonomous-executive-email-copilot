"""The server-rendered UI: the demo path an interviewer actually walks.

Covers the whole journey — landing, signup, connect the demo mailbox, read the
triaged inbox, approve an action — plus the two things that are easy to get
wrong and invisible when they break: the session gate on ``/app/*`` and CSRF on
every form post.
"""

from __future__ import annotations

import re
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.saas.deps import SESSION_COOKIE
from app.saas.repository import ProcessedMessageRepository, ProposedActionRepository

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def csrf_from(html: str) -> str:
    match = CSRF_RE.search(html)
    assert match, "every page with a form must embed a CSRF token"
    return match.group(1)


@pytest.fixture
def client():
    """A browser-like client: keeps cookies, does not chase redirects."""
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def signed_in(client):
    """A fresh workspace with an owner signed in. Returns (client, user_email)."""
    email = f"owner_{uuid.uuid4().hex[:10]}@northwind.example"
    page = client.get("/signup").text
    response = client.post(
        "/signup",
        data={
            "csrf_token": csrf_from(page),
            "org_name": "Northwind Industries",
            "full_name": "Alex Chen",
            "email": email,
            "password": "a-strong-password",
        },
    )
    assert response.status_code == 303
    return client, email


@pytest.fixture
def with_demo_mailbox(signed_in):
    """A signed-in workspace with the demo mailbox connected and synced."""
    client, email = signed_in
    page = client.get("/app/connect").text
    response = client.post("/app/connect/demo", data={"csrf_token": csrf_from(page)})
    assert response.status_code == 303
    assert response.headers["location"] == "/app/inbox"
    return client, email


# --------------------------------------------------------------------------- #
# Public pages
# --------------------------------------------------------------------------- #
class TestPublicPages:
    def test_root_is_the_landing_page(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        # The old behaviour was a redirect into an ops console.
        assert "runs itself" in response.text
        assert "Start free trial" in response.text

    def test_pricing_lists_every_plan(self, client):
        from app.saas import licensing

        response = client.get("/pricing")
        assert response.status_code == 200
        for plan in licensing.PLANS.values():
            assert plan.name in response.text

    def test_welcome_redirects_to_root(self, client):
        """The landing page moved; previously-shared links must still work."""
        response = client.get("/welcome")
        assert response.status_code == 301
        assert response.headers["location"] == "/"

    def test_stylesheet_is_served(self, client):
        response = client.get("/static/app.css")
        assert response.status_code == 200
        assert "text/css" in response.headers["content-type"]

    def test_login_and_signup_render(self, client):
        assert client.get("/login").status_code == 200
        assert client.get("/signup").status_code == 200


# --------------------------------------------------------------------------- #
# The session gate
# --------------------------------------------------------------------------- #
class TestSessionGate:
    @pytest.mark.parametrize(
        "path",
        ["/app/inbox", "/app/approvals", "/app/activity", "/app/settings", "/app/connect"],
    )
    def test_anonymous_visitor_is_sent_to_login(self, client, path):
        response = client.get(path)
        assert response.status_code == 303
        assert response.headers["location"].startswith("/login?next=")

    def test_login_preserves_the_original_destination(self, client):
        response = client.get("/app/settings")
        assert "next=/app/settings" in response.headers["location"]

    def test_login_sets_an_httponly_session_cookie(self, client):
        email = f"user_{uuid.uuid4().hex[:10]}@northwind.example"
        page = client.get("/signup").text
        client.post(
            "/signup",
            data={
                "csrf_token": csrf_from(page),
                "org_name": "Acme",
                "full_name": "A Person",
                "email": email,
                "password": "a-strong-password",
            },
        )
        client.post("/logout", data={"csrf_token": csrf_from(client.get("/app/inbox").text)})

        page = client.get("/login").text
        response = client.post(
            "/login",
            data={"csrf_token": csrf_from(page), "email": email, "password": "a-strong-password"},
        )
        assert response.status_code == 303
        cookie_header = response.headers["set-cookie"]
        assert SESSION_COOKIE in cookie_header
        # Cookie attribute names/values are case-insensitive (RFC 6265).
        lowered = cookie_header.lower()
        assert "httponly" in lowered, "the session token must be unreadable from JS"
        assert "samesite=lax" in lowered, "SameSite is half of the CSRF defence"
        # The inbox is reachable now.
        assert client.get("/app/inbox").status_code == 200

    def test_bad_password_re_renders_with_an_error(self, client):
        page = client.get("/login").text
        response = client.post(
            "/login",
            data={
                "csrf_token": csrf_from(page),
                "email": "nobody@northwind.example",
                "password": "wrong-password",
            },
        )
        assert response.status_code == 401
        assert "Invalid email or password" in response.text
        assert SESSION_COOKIE not in response.headers.get("set-cookie", "")

    def test_logout_clears_the_session(self, signed_in):
        client, _email = signed_in
        assert client.get("/app/inbox").status_code == 200
        client.post("/logout", data={"csrf_token": csrf_from(client.get("/app/inbox").text)})
        assert client.get("/app/inbox").status_code == 303


# --------------------------------------------------------------------------- #
# CSRF
# --------------------------------------------------------------------------- #
class TestCsrf:
    def test_forged_token_is_rejected(self, signed_in):
        client, _ = signed_in
        response = client.post("/app/connect/demo", data={"csrf_token": "not-a-real-token"})
        assert response.status_code == 403

    def test_missing_token_is_rejected(self, signed_in):
        client, _ = signed_in
        response = client.post("/app/connect/demo", data={})
        assert response.status_code == 403

    def test_a_token_from_another_session_is_rejected(self, signed_in):
        """The core of the protection: a token must belong to the session using it."""
        client, _ = signed_in
        other = TestClient(app, follow_redirects=False)
        stolen = csrf_from(other.get("/login").text)

        response = client.post("/app/connect/demo", data={"csrf_token": stolen})
        assert response.status_code == 403


# --------------------------------------------------------------------------- #
# The demo mailbox and the inbox
# --------------------------------------------------------------------------- #
class TestDemoMailbox:
    def test_connect_page_always_offers_the_demo_mailbox(self, signed_in):
        client, _ = signed_in
        response = client.get("/app/connect")
        assert response.status_code == 200
        assert "Demo mailbox" in response.text
        # Real providers are unconfigured in tests and must say so rather than
        # erroring when clicked.
        assert "Not configured" in response.text

    def test_connecting_the_demo_mailbox_triages_it(self, with_demo_mailbox):
        from app.saas.repository import UserRepository

        client, email = with_demo_mailbox
        org_id = UserRepository().get_by_email_global(email)["org_id"]

        from app.copilot.providers.demo import demo_message_count

        messages = ProcessedMessageRepository().list_for_org(org_id)
        # Against the fixture's own size, not a literal: the demo mailbox is
        # content and grows. A whole mailbox must arrive — the provider interface
        # defaults to limit=25, which silently truncated this once already, and
        # a half-synced inbox looks complete because nothing reports the gap.
        assert messages["total"] == demo_message_count()

        pending = ProposedActionRepository().list_for_org(org_id, status="proposed")
        assert pending["total"] > 0, "the demo must leave work in the approval queue"

    def test_inbox_shows_messages_and_the_copilot_panel(self, with_demo_mailbox):
        client, _ = with_demo_mailbox
        response = client.get("/app/inbox")
        assert response.status_code == 200
        assert "indemnification" in response.text  # a message from the fixture
        assert "Copilot" in response.text
        assert "Priority" in response.text and "Risk" in response.text

    def test_inbox_without_a_mailbox_explains_what_to_do(self, signed_in):
        client, _ = signed_in
        response = client.get("/app/inbox")
        assert response.status_code == 200
        assert "No mailbox connected" in response.text

    def test_selecting_a_message_renders_that_message(self, with_demo_mailbox):
        from app.saas.repository import UserRepository

        client, email = with_demo_mailbox
        org_id = UserRepository().get_by_email_global(email)["org_id"]
        messages = ProcessedMessageRepository().list_for_org(org_id)["messages"]
        target = next(m for m in messages if "phishing" in (m["subject"] or "").lower())

        response = client.get(f"/app/inbox?message={target['id']}")
        assert response.status_code == 200
        assert target["subject"] in response.text

    def test_authored_draft_replaces_the_generic_policy_sentence(self, with_demo_mailbox):
        """The policy emits one canned line for every reply; the demo must not."""
        from app.saas.repository import UserRepository

        _client, email = with_demo_mailbox
        org_id = UserRepository().get_by_email_global(email)["org_id"]

        replies = [
            a
            for a in ProposedActionRepository().list_for_org(org_id, limit=200)["actions"]
            if a["action_type"] == "reply"
        ]
        assert replies, "the demo inbox must produce at least one drafted reply"
        for reply in replies:
            assert reply["content"]
            assert "We are treating this as urgent" not in reply["content"]


# --------------------------------------------------------------------------- #
# Approvals
# --------------------------------------------------------------------------- #
class TestApprovals:
    def test_approving_executes_the_action(self, with_demo_mailbox):
        from app.saas.repository import UserRepository

        client, email = with_demo_mailbox
        org_id = UserRepository().get_by_email_global(email)["org_id"]
        actions = ProposedActionRepository()

        pending = actions.list_for_org(org_id, status="proposed")["actions"]
        target = next(a for a in pending if a["action_type"] == "reply")

        page = client.get("/app/approvals").text
        response = client.post(
            f"/app/actions/{target['id']}/approve", data={"csrf_token": csrf_from(page)}
        )
        assert response.status_code == 303

        updated = actions.get(org_id, target["id"])
        assert updated["status"] == "executed"
        assert updated["outcome"] == "approved"
        assert updated["execution_ref"], "an executed action must record its provider reference"

    def test_rejecting_records_the_decision_without_sending(self, with_demo_mailbox):
        from app.saas.repository import UserRepository

        client, email = with_demo_mailbox
        org_id = UserRepository().get_by_email_global(email)["org_id"]
        actions = ProposedActionRepository()

        target = actions.list_for_org(org_id, status="proposed")["actions"][0]
        page = client.get("/app/approvals").text
        client.post(f"/app/actions/{target['id']}/reject", data={"csrf_token": csrf_from(page)})

        updated = actions.get(org_id, target["id"])
        assert updated["status"] == "rejected"
        assert not updated["execution_ref"]

    def test_approvals_page_lists_what_is_waiting(self, with_demo_mailbox):
        client, _ = with_demo_mailbox
        response = client.get("/app/approvals")
        assert response.status_code == 200
        assert "Approve" in response.text


# --------------------------------------------------------------------------- #
# Supporting pages
# --------------------------------------------------------------------------- #
class TestSupportingPages:
    def test_activity_shows_the_audit_trail(self, with_demo_mailbox):
        client, _ = with_demo_mailbox
        response = client.get("/app/activity")
        assert response.status_code == 200
        assert "mailbox.connect" in response.text

    def test_failed_sign_in_appears_on_the_activity_page(self, signed_in):
        """The Activity page claims every sign-in attempt lands there — that
        must include *failed* web sign-ins, attributed to the account's org."""
        client, email = signed_in
        client.post("/logout", data={"csrf_token": csrf_from(client.get("/app/inbox").text)})

        page = client.get("/login").text
        response = client.post(
            "/login",
            data={
                "csrf_token": csrf_from(page),
                "email": email,
                "password": "not-the-password",
                "next": "/app/inbox",
            },
        )
        assert response.status_code == 401

        # Sign back in and check the trail.
        page = client.get("/login").text
        client.post(
            "/login",
            data={
                "csrf_token": csrf_from(page),
                "email": email,
                "password": "a-strong-password",
                "next": "/app/inbox",
            },
        )
        activity = client.get("/app/activity").text
        assert "auth.login_failed" in activity

    def test_settings_shows_plan_and_members(self, with_demo_mailbox):
        client, email = with_demo_mailbox
        response = client.get("/app/settings")
        assert response.status_code == 200
        assert "Northwind Industries" in response.text
        assert email in response.text
        assert "Trial" in response.text or "trial" in response.text

    def test_app_root_redirects_to_the_inbox(self, signed_in):
        client, _ = signed_in
        response = client.get("/app")
        assert response.status_code == 307
        assert response.headers["location"] == "/app/inbox"


# --------------------------------------------------------------------------- #
# Open-redirect hardening
# --------------------------------------------------------------------------- #
class TestRedirectSafety:
    @pytest.mark.parametrize(
        "hostile",
        ["https://evil.example/phish", "//evil.example/phish", "http://evil.example"],
    )
    def test_login_will_not_bounce_to_another_site(self, client, hostile):
        """Otherwise the login page becomes a credible phishing redirector."""
        email = f"user_{uuid.uuid4().hex[:10]}@northwind.example"
        page = client.get("/signup").text
        client.post(
            "/signup",
            data={
                "csrf_token": csrf_from(page),
                "org_name": "Acme",
                "full_name": "A Person",
                "email": email,
                "password": "a-strong-password",
            },
        )
        client.post("/logout", data={"csrf_token": csrf_from(client.get("/app/inbox").text)})

        page = client.get("/login").text
        response = client.post(
            "/login",
            data={
                "csrf_token": csrf_from(page),
                "email": email,
                "password": "a-strong-password",
                "next": hostile,
            },
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/app/inbox"


# --------------------------------------------------------------------------- #
# Mailbox management
# --------------------------------------------------------------------------- #
class TestMailboxManagement:
    def test_connecting_an_unconfigured_provider_explains_rather_than_crashes(self, signed_in):
        """Clicking Gmail on a server with no OAuth secrets is a normal state.

        It used to raise a bare 400 from the API. The page should say what is
        wrong and still offer the demo mailbox.
        """
        client, _ = signed_in
        page = client.get("/app/connect").text
        response = client.post("/app/connect/google", data={"csrf_token": csrf_from(page)})
        assert response.status_code == 400
        assert "not configured" in response.text.lower()
        assert "Demo mailbox" in response.text

    def test_connecting_an_unknown_provider_is_a_404(self, signed_in):
        client, _ = signed_in
        page = client.get("/app/connect").text
        response = client.post("/app/connect/carrier-pigeon", data={"csrf_token": csrf_from(page)})
        assert response.status_code == 404

    def test_sync_all_is_idempotent(self, with_demo_mailbox):
        """Re-syncing must not duplicate messages or re-propose decided actions."""
        from app.saas.repository import UserRepository

        client, email = with_demo_mailbox
        org_id = UserRepository().get_by_email_global(email)["org_id"]
        before = ProcessedMessageRepository().list_for_org(org_id)["total"]

        page = client.get("/app/inbox").text
        response = client.post("/app/sync", data={"csrf_token": csrf_from(page)})
        assert response.status_code == 303

        assert ProcessedMessageRepository().list_for_org(org_id)["total"] == before

    def test_syncing_one_connection(self, with_demo_mailbox):
        from app.saas.repository import MailboxRepository, UserRepository

        client, email = with_demo_mailbox
        org_id = UserRepository().get_by_email_global(email)["org_id"]
        connection = MailboxRepository().list_for_org(org_id)[0]

        page = client.get("/app/connect").text
        response = client.post(
            f"/app/mailboxes/{connection['id']}/sync", data={"csrf_token": csrf_from(page)}
        )
        assert response.status_code == 303

    def test_syncing_a_connection_that_does_not_exist_is_harmless(self, with_demo_mailbox):
        client, _ = with_demo_mailbox
        page = client.get("/app/connect").text
        response = client.post(
            "/app/mailboxes/does-not-exist/sync", data={"csrf_token": csrf_from(page)}
        )
        assert response.status_code == 303

    def test_disconnecting_removes_the_mailbox(self, with_demo_mailbox):
        from app.saas.repository import MailboxRepository, UserRepository

        client, email = with_demo_mailbox
        org_id = UserRepository().get_by_email_global(email)["org_id"]
        connection = MailboxRepository().list_for_org(org_id)[0]

        page = client.get("/app/connect").text
        response = client.post(
            f"/app/mailboxes/{connection['id']}/disconnect", data={"csrf_token": csrf_from(page)}
        )
        assert response.status_code == 303
        assert MailboxRepository().list_for_org(org_id) == []

    def test_disconnecting_removes_messages_and_actions_too(self, with_demo_mailbox):
        """The UI promises cleanup, and reconnect must not duplicate the inbox.

        Orphaned rows are worse than clutter: the pending badge keeps counting
        actions whose mailbox is gone, approving one 404s, and a reconnect
        mints a new connection id so every message is 'new' to the dedup key.
        """
        from app.saas.repository import (
            MailboxRepository,
            ProposedActionRepository,
            UserRepository,
        )

        client, email = with_demo_mailbox
        org_id = UserRepository().get_by_email_global(email)["org_id"]
        connection = MailboxRepository().list_for_org(org_id)[0]
        assert ProcessedMessageRepository().list_for_org(org_id)["total"] > 0

        page = client.get("/app/connect").text
        client.post(
            f"/app/mailboxes/{connection['id']}/disconnect", data={"csrf_token": csrf_from(page)}
        )

        assert ProcessedMessageRepository().list_for_org(org_id)["total"] == 0
        assert ProposedActionRepository().list_for_org(org_id)["total"] == 0

        # Reconnecting starts clean: same message count as the first connect,
        # not double.
        page = client.get("/app/connect").text
        client.post("/app/connect/demo", data={"csrf_token": csrf_from(page)})
        from app.copilot.providers.demo import demo_message_count

        assert ProcessedMessageRepository().list_for_org(org_id)["total"] == demo_message_count()


# --------------------------------------------------------------------------- #
# Role gating
# --------------------------------------------------------------------------- #
class TestRoleGating:
    def _demote_to_member(self, email: str) -> None:
        from app.saas.repository import UserRepository

        users = UserRepository()
        user = users.get_by_email_global(email)
        users.update_role(user["org_id"], user["id"], "member")

    def test_a_member_cannot_connect_a_mailbox(self, signed_in):
        client, email = signed_in
        page = client.get("/app/connect").text
        self._demote_to_member(email)

        response = client.post("/app/connect/demo", data={"csrf_token": csrf_from(page)})
        assert response.status_code == 403

    def test_a_member_cannot_approve(self, with_demo_mailbox):
        from app.saas.repository import UserRepository

        client, email = with_demo_mailbox
        org_id = UserRepository().get_by_email_global(email)["org_id"]
        target = ProposedActionRepository().list_for_org(org_id, status="proposed")["actions"][0]

        page = client.get("/app/approvals").text
        self._demote_to_member(email)

        response = client.post(
            f"/app/actions/{target['id']}/approve", data={"csrf_token": csrf_from(page)}
        )
        assert response.status_code == 403
        # And the action is untouched.
        assert ProposedActionRepository().get(org_id, target["id"])["status"] == "proposed"

    def test_a_member_still_sees_the_inbox_read_only(self, with_demo_mailbox):
        client, email = with_demo_mailbox
        self._demote_to_member(email)

        response = client.get("/app/inbox")
        assert response.status_code == 200
        assert "Waiting on an admin" in response.text

    def test_a_member_cannot_read_the_audit_log(self, with_demo_mailbox):
        client, email = with_demo_mailbox
        self._demote_to_member(email)

        response = client.get("/app/activity")
        assert response.status_code == 200
        assert "Admins only" in response.text


# --------------------------------------------------------------------------- #
# Missing / stale references
# --------------------------------------------------------------------------- #
class TestMissingReferences:
    def test_approving_an_action_that_does_not_exist_is_a_404(self, with_demo_mailbox):
        client, _ = with_demo_mailbox
        page = client.get("/app/approvals").text
        response = client.post(
            "/app/actions/no-such-action/approve", data={"csrf_token": csrf_from(page)}
        )
        assert response.status_code == 404

    def test_web_errors_render_a_page_not_raw_json(self, with_demo_mailbox):
        """A double-clicked Approve (or any web-path error) must land the user
        on a page with a way back — the JSON error contract is for the API."""
        client, _ = with_demo_mailbox
        page = client.get("/app/approvals").text
        response = client.post(
            "/app/actions/no-such-action/approve", data={"csrf_token": csrf_from(page)}
        )
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("text/html")
        assert "Back to the inbox" in response.text

        # And an unknown /app page too.
        response = client.get("/app/no-such-page")
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("text/html")

    def test_api_errors_keep_the_json_contract(self, with_demo_mailbox):
        client, _ = with_demo_mailbox
        response = client.get("/replay/definitely-not-an-episode")
        assert response.headers["content-type"].startswith("application/json")
        assert "detail" in response.json()

    def test_rejecting_an_action_that_does_not_exist_is_a_404(self, with_demo_mailbox):
        client, _ = with_demo_mailbox
        page = client.get("/app/approvals").text
        response = client.post(
            "/app/actions/no-such-action/reject", data={"csrf_token": csrf_from(page)}
        )
        assert response.status_code == 404

    def test_an_unknown_message_id_falls_back_to_the_first_message(self, with_demo_mailbox):
        """A stale bookmark should show the inbox, not an error."""
        client, _ = with_demo_mailbox
        response = client.get("/app/inbox?message=no-such-message")
        assert response.status_code == 200
        assert "Copilot" in response.text


# --------------------------------------------------------------------------- #
# Plan enforcement
# --------------------------------------------------------------------------- #
class TestPlanEnforcement:
    """The license terms are enforced, not decorative: an expired plan stops
    the value loop (sync, approve), and a plan without a feature loses the
    feature. Sign-in and settings stay open — an admin needs those exactly
    when the plan has lapsed."""

    @staticmethod
    def _org_id(email: str) -> str:
        from app.saas.repository import UserRepository

        return UserRepository().get_by_email_global(email)["org_id"]

    @staticmethod
    def _rewrite_license(org_id: str, *, features: list[str] | None = None, expired: bool = False):
        from datetime import datetime, timedelta, timezone

        from app.saas.repository import LicenseRepository

        repo = LicenseRepository()
        row = repo.get_active_for_org(org_id)
        assert row, "signup should have minted a trial license"
        delta = timedelta(days=-1) if expired else timedelta(days=14)
        repo.upsert(
            org_id=org_id,
            key_id=row["key_id"],
            plan=row["plan"],
            seats=row["seats"],
            features=row["features"] if features is None else features,
            expires_at_iso=(datetime.now(timezone.utc) + delta).isoformat(),
        )

    def test_expired_plan_blocks_sync(self, with_demo_mailbox):
        client, email = with_demo_mailbox
        self._rewrite_license(self._org_id(email), expired=True)

        page = client.get("/app/connect").text
        response = client.post("/app/sync", data={"csrf_token": csrf_from(page)})
        assert response.status_code == 402
        assert "expired" in response.text.lower()

    def test_expired_plan_blocks_approve_but_not_reject(self, with_demo_mailbox):
        client, email = with_demo_mailbox
        org_id = self._org_id(email)
        actions = ProposedActionRepository().list_for_org(org_id, status="proposed")["actions"]
        assert len(actions) >= 2
        self._rewrite_license(org_id, expired=True)

        page = client.get("/app/approvals").text
        approve = client.post(
            f"/app/actions/{actions[0]['id']}/approve", data={"csrf_token": csrf_from(page)}
        )
        assert approve.status_code == 402

        # Rejection only records a decision; it stays available.
        reject = client.post(
            f"/app/actions/{actions[1]['id']}/reject", data={"csrf_token": csrf_from(page)}
        )
        assert reject.status_code == 303

    def test_plan_without_audit_log_loses_the_activity_page(self, with_demo_mailbox):
        client, email = with_demo_mailbox
        self._rewrite_license(
            self._org_id(email), features=["approvals", "analytics"]
        )  # a Team-shaped plan

        response = client.get("/app/activity")
        assert response.status_code == 403
        assert "not included in your current plan" in response.text

        # The rest of the app is untouched.
        assert client.get("/app/inbox").status_code == 200
        assert client.get("/app/settings").status_code == 200
