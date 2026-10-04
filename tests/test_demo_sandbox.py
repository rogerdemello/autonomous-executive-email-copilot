"""The one-click demo: a private workspace per visitor.

"Try the live demo" used to lead to a login form pre-filled with a shared
account's password — and the pre-fill only appeared if that account had been
seeded, so on a deployment whose database was ever empty the visitor got a blank
form and no way to get credentials. These tests pin the replacement and, more
than that, the things that make it safe to hang off an unauthenticated button:
every visitor is isolated, the thing is bounded, it cleans up after itself, it
cannot spend the deployment's money, and it cannot reach the outside world.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.security import SANDBOXES_PER_WINDOW, sandbox_rate_limiter
from app.main import app
from app.saas import sandbox
from app.saas.data_lifecycle import DataLifecycleService
from app.saas.deps import SESSION_COOKIE
from app.saas.repository import (
    MailboxRepository,
    OrganizationRepository,
    ProposedActionRepository,
    UserRepository,
)

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')
ORGS = OrganizationRepository()


def csrf_from(html: str) -> str:
    match = CSRF_RE.search(html)
    assert match, "no csrf token on the page"
    return match.group(1)


def _wipe_sandboxes() -> None:
    far_future = "9999-01-01T00:00:00+00:00"
    for org_id in ORGS.expired_sandbox_ids(far_future):
        DataLifecycleService().delete_org(org_id)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """Every test starts and ends with no sandboxes and a fresh rate limit.

    The database is shared by the whole session, and the cap counts *all* live
    sandboxes — so a leak here would turn into a mysterious 503 many tests later.
    """
    monkeypatch.delenv("DEMO_LOGIN_ENABLED", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    sandbox_rate_limiter.reset()
    _wipe_sandboxes()
    yield
    _wipe_sandboxes()
    sandbox_rate_limiter.reset()


@pytest.fixture
def client():
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def frozen_window(monkeypatch):
    """Pin the limiter's clock so a burst cannot straddle a window boundary."""
    import app.core.security as security

    real_allow = security.FixedWindowRateLimiter.allow
    monkeypatch.setattr(
        security.FixedWindowRateLimiter,
        "allow",
        lambda self, key, limit, now=None: real_allow(self, key, limit, now=1_800_000_000.0),
    )


def open_demo(client: TestClient):
    """What the landing page's button does: read a form token, POST it."""
    page = client.get("/").text
    return client.post("/demo", data={"csrf_token": csrf_from(page)})


def org_of(client: TestClient) -> str:
    from app.saas.auth import AuthService

    return AuthService().resolve(client.cookies.get(SESSION_COOKIE))["org_id"]


def sign_up_a_real_customer(client: TestClient) -> str:
    """A real workspace, through the signup form. Returns its org id."""
    page = client.get("/signup").text
    response = client.post(
        "/signup",
        data={
            "csrf_token": csrf_from(page),
            "org_name": "Acme Real Co",
            "full_name": "Rae Customer",
            "email": f"rae-{uuid.uuid4().hex[:10]}@acmereal.io",
            "password": "a-real-password-1",
        },
    )
    assert response.status_code == 303
    return org_of(client)


class TestOneClickDemo:
    def test_the_landing_page_offers_a_form_not_a_login_link(self, client):
        """The old CTA was `<a href="/login">` — a dead end for a stranger."""
        body = client.get("/").text
        assert 'action="/demo"' in body
        assert "Try the live demo" in body
        assert 'href="/login">Try the live demo' not in body

    def test_one_click_lands_in_a_populated_inbox(self, client):
        response = open_demo(client)
        assert response.status_code == 303
        assert response.headers["location"] == "/app/inbox?tour=1"

        inbox = client.get("/app/inbox?tour=1")
        assert inbox.status_code == 200
        assert "Demo sandbox" in inbox.text
        assert "Two minutes in the demo" in inbox.text

        approvals = client.get("/app/approvals").text
        assert "awaiting approval" in approvals
        assert "Nothing waiting on you" not in approvals

    def test_it_asks_for_no_credentials_and_none_exist(self, client):
        """The owner's address is undeliverable and its password was never kept."""
        open_demo(client)
        from app.saas.auth import AuthService

        user = AuthService().resolve(client.cookies.get(SESSION_COOKIE))
        assert user["email"].endswith("@sandbox.invalid")
        assert user["email"] != "alex.chen@northwind.example"

    def test_get_never_builds_anything(self, client):
        """Crawlers and link unfurlers GET. A GET that built a workspace would
        build one per bot."""
        before = ORGS.count_sandboxes()
        page = client.get("/demo")
        assert page.status_code == 200
        assert "Open the live demo" in page.text
        assert ORGS.count_sandboxes() == before

    def test_post_needs_a_form_token(self, client):
        assert client.post("/demo").status_code == 403
        assert client.post("/demo", data={"csrf_token": "forged"}).status_code == 403
        assert ORGS.count_sandboxes() == 0

    def test_the_sign_in_page_offers_it_too(self, client):
        body = client.get("/login").text
        assert 'action="/demo"' in body
        assert "no sign-up" in body

    def test_a_second_click_reuses_the_workspace(self, client):
        open_demo(client)
        first = org_of(client)
        again = open_demo(client)
        assert again.status_code == 303
        assert org_of(client) == first
        assert ORGS.count_sandboxes() == 1


class TestOnALockedDownDeployment:
    """The production blueprint sets API_AUTH_TOKEN. The demo button 401'd there."""

    def test_the_button_works_with_the_operator_token_set(self, client, monkeypatch):
        """Shipped broken: every test ran with the token unset, and the live site
        answered {"detail":"Missing or invalid API token"} to the first click."""
        monkeypatch.setenv("API_AUTH_TOKEN", "locked-down-operator-token")
        response = open_demo(client)
        assert response.status_code == 303, response.text
        assert response.headers["location"] == "/app/inbox?tour=1"
        assert client.get("/app/inbox").status_code == 200
        assert ORGS.count_sandboxes() == 1


class TestWhenTheDemoIsOff:
    @pytest.mark.parametrize("method", ["get", "post"])
    def test_the_route_is_absent(self, client, monkeypatch, method):
        monkeypatch.setenv("DEMO_LOGIN_ENABLED", "false")
        page = client.get("/login").text
        response = (
            client.get("/demo")
            if method == "get"
            else client.post("/demo", data={"csrf_token": csrf_from(page)})
        )
        assert response.status_code == 404
        # A page, not {"detail": "Not Found"}: /demo is a web path.
        assert response.headers["content-type"].startswith("text/html")

    def test_no_page_links_to_a_dead_end(self, client, monkeypatch):
        monkeypatch.setenv("DEMO_LOGIN_ENABLED", "false")
        for path in ("/", "/login"):
            body = client.get(path).text
            assert 'action="/demo"' not in body, path
            assert "Try the live demo" not in body, path
        assert "Start free" in client.get("/").text

    def test_production_must_opt_in(self, client, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("AUTH_SECRET_KEY", "a-long-random-production-secret")
        assert client.get("/demo").status_code == 404

        monkeypatch.setenv("DEMO_LOGIN_ENABLED", "true")
        assert client.get("/demo").status_code == 200
        assert open_demo(client).status_code == 303


class TestEveryVisitorIsIsolated:
    def test_two_visitors_get_two_workspaces(self):
        a, b = TestClient(app, follow_redirects=False), TestClient(app, follow_redirects=False)
        open_demo(a)
        open_demo(b)
        assert org_of(a) != org_of(b)
        assert ORGS.count_sandboxes() == 2

    def test_what_one_visitor_approves_the_next_still_has(self):
        """The shared account's flaw: whoever got there first emptied the queue."""
        a, b = TestClient(app, follow_redirects=False), TestClient(app, follow_redirects=False)
        open_demo(a)
        open_demo(b)
        actions = ProposedActionRepository()
        before_b = actions.list_pending_with_messages(org_of(b), limit=100)["total"]
        assert before_b > 0

        queue = actions.list_pending_with_messages(org_of(a), limit=100)["items"]
        page = a.get("/app/approvals").text
        for item in queue:
            a.post(
                f"/app/actions/{item['action']['id']}/approve",
                data={"csrf_token": csrf_from(page), "message": ""},
            )
        assert actions.list_pending_with_messages(org_of(a), limit=100)["total"] == 0
        assert actions.list_pending_with_messages(org_of(b), limit=100)["total"] == before_b

    def test_a_real_customer_is_never_signed_out_by_the_demo_button(self):
        customer = TestClient(app, follow_redirects=False)
        org_id = sign_up_a_real_customer(customer)
        cookie = customer.cookies.get(SESSION_COOKIE)

        response = open_demo(customer)
        assert response.status_code == 303
        assert response.headers["location"] == "/app/inbox"  # not the tour
        assert customer.cookies.get(SESSION_COOKIE) == cookie
        assert org_of(customer) == org_id
        assert ORGS.count_sandboxes() == 0


class TestWhatASandboxMayNotDo:
    """Whoever holds a sandbox is an anonymous stranger."""

    def _settings_token(self, client) -> str:
        return csrf_from(client.get("/app/settings").text)

    @pytest.mark.parametrize(
        "path, data",
        [
            ("/app/members/invite", {"email": "x@evil.example", "full_name": "x", "role": "admin"}),
            (
                "/app/settings/password",
                {"current_password": "x", "new_password": "hijacked-8chars"},
            ),
            ("/app/settings/license", {"license_key": "anything"}),
            ("/app/settings/escalation", {"role": "legal_team", "email": "a@b.example"}),
            ("/app/settings/delete-org", {"confirm": "whatever"}),
            ("/app/connect/google", {}),
        ],
    )
    def test_administrative_actions_are_refused(self, client, path, data):
        open_demo(client)
        response = client.post(path, data={"csrf_token": self._settings_token(client), **data})
        assert response.status_code == 403, path
        assert "demo sandbox" in response.text.lower()

    def test_the_export_is_refused(self, client):
        open_demo(client)
        assert client.get("/app/settings/export").status_code == 403

    def test_the_api_is_fenced_the_same_way(self, client):
        open_demo(client)
        token = client.cookies.get(SESSION_COOKIE)
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/org/export", headers=headers).status_code == 403
        assert (
            client.post(
                "/org/members",
                json={
                    "email": "a@evil.example",
                    "full_name": "x",
                    "role": "member",
                    "temp_password": "temporary-password",
                },
                headers=headers,
            ).status_code
            == 403
        )

    def test_the_demo_itself_still_works(self, client):
        """Fencing off the account must not fence off the point of it."""
        open_demo(client)
        queue = ProposedActionRepository().list_pending_with_messages(org_of(client), limit=100)
        action_id = queue["items"][0]["action"]["id"]
        page = client.get("/app/approvals").text
        response = client.post(
            f"/app/actions/{action_id}/approve",
            data={"csrf_token": csrf_from(page), "message": ""},
        )
        assert response.status_code == 303

    def test_signing_out_deletes_the_sandbox(self, client):
        open_demo(client)
        org_id = org_of(client)
        page = client.get("/app/inbox").text
        assert client.post("/logout", data={"csrf_token": csrf_from(page)}).status_code == 303
        assert ORGS.get(org_id) is None
        assert UserRepository().list_for_org(org_id) == []

    def test_a_visitor_can_still_go_and_sign_up_for_real(self, client):
        """The banner's call to action must not bounce them back into the sandbox."""
        open_demo(client)
        assert client.get("/signup").status_code == 200
        assert client.get("/login").status_code == 200


class TestBounds:
    def test_one_address_cannot_open_the_demo_without_limit(self, frozen_window):
        for _ in range(SANDBOXES_PER_WINDOW):
            assert open_demo(TestClient(app, follow_redirects=False)).status_code == 303
        refused = open_demo(TestClient(app, follow_redirects=False))
        assert refused.status_code == 429
        assert "few minutes" in refused.text
        assert ORGS.count_sandboxes() == SANDBOXES_PER_WINDOW

    def test_at_capacity_it_refuses_rather_than_evicting_a_live_visitor(self, client, monkeypatch):
        existing = TestClient(app, follow_redirects=False)
        open_demo(existing)
        keep = org_of(existing)

        monkeypatch.setattr(sandbox, "MAX_LIVE_SANDBOXES", 1)
        refused = open_demo(client)
        assert refused.status_code == 503
        assert "busy" in refused.text
        assert ORGS.get(keep) is not None, "a live visitor's workspace was deleted to make room"
        assert ORGS.count_sandboxes() == 1

    def test_a_failed_build_leaves_nothing_behind(self, client, monkeypatch):
        import app.saas.demo_seed as demo_seed

        def boom(*_a, **_k):
            raise RuntimeError("the mailbox exploded halfway")

        monkeypatch.setattr(demo_seed, "populate_demo_workspace", boom)
        response = open_demo(client)
        assert response.status_code == 500
        assert "open the demo just now" in response.text  # the page says so; not a bare JSON 500
        assert ORGS.count_sandboxes() == 0
        assert SESSION_COOKIE not in client.cookies

    def test_a_sandbox_lives_exactly_as_long_as_the_session_that_opened_it(self, client):
        open_demo(client)
        org = ORGS.get(org_of(client))
        expires = datetime.fromisoformat(org["sandbox_expires_at"])
        created = datetime.fromisoformat(org["created_at"])
        lifetime = (expires - created).total_seconds()
        assert abs(lifetime - sandbox.sandbox_lifetime().total_seconds()) < 5


class TestCleanup:
    def test_an_expired_sandbox_is_deleted_completely(self, client):
        open_demo(client)
        org_id = org_of(client)
        lifecycle = DataLifecycleService()
        before = lifecycle.remaining_rows(org_id)
        # A real triaged workspace: messages, actions, a mailbox, escalation contacts.
        assert {
            "saas_processed_messages",
            "saas_proposed_actions",
            "saas_escalation_contacts",
        } <= set(before)
        deadline = datetime.fromisoformat(ORGS.get(org_id)["sandbox_expires_at"])

        assert sandbox.purge_expired(now=deadline + timedelta(seconds=1)) == 1
        assert ORGS.get(org_id) is None
        assert lifecycle.remaining_rows(org_id) == {}

    def test_only_expired_sandboxes_are_purged(self):
        """A live visitor and a paying customer both survive someone else's sweep."""
        live = TestClient(app, follow_redirects=False)
        open_demo(live)
        live_org = org_of(live)
        customer = TestClient(app, follow_redirects=False)
        customer_org = sign_up_a_real_customer(customer)

        assert sandbox.purge_expired() == 0
        assert ORGS.get(live_org) is not None
        # Even a sweep from the far future cannot touch a real workspace: its
        # deadline is NULL, which no comparison selects.
        sandbox.purge_expired(now=datetime(2999, 1, 1, tzinfo=timezone.utc))
        assert ORGS.get(live_org) is None
        assert ORGS.get(customer_org) is not None

    def test_opening_a_new_one_sweeps_the_old(self, client):
        stale = TestClient(app, follow_redirects=False)
        open_demo(stale)
        stale_org = org_of(stale)
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).replace(microsecond=0)
        from app.core.db import get_session
        from app.saas.models_db import Organization

        with get_session() as session:
            session.get(Organization, stale_org).sandbox_expires_at = past.isoformat()

        open_demo(client)
        assert ORGS.get(stale_org) is None
        assert ORGS.count_sandboxes() == 1

    def test_the_background_worker_sweeps_them_too(self, client):
        from app.saas.sync_worker import BackgroundSyncWorker

        open_demo(client)
        org_id = org_of(client)
        deadline = datetime.fromisoformat(ORGS.get(org_id)["sandbox_expires_at"])
        BackgroundSyncWorker().sync_due_connections(now=deadline + timedelta(minutes=1))
        assert ORGS.get(org_id) is None


class TestNoSpendAndNoReach:
    def test_the_background_worker_never_syncs_a_sandbox_mailbox(self, client):
        """It is a fixture mailbox: a sweep re-triages it forever to learn nothing."""
        open_demo(client)
        sandbox_org = org_of(client)
        customer = TestClient(app, follow_redirects=False)
        customer_org = sign_up_a_real_customer(customer)
        MailboxRepository().upsert_connection(
            org_id=customer_org,
            provider="fake",
            account_email="exec@acmereal.io",
            connected_by=None,
            access_token_enc=None,
            refresh_token_enc=None,
            token_expires_at=None,
            scopes=None,
        )
        swept = {c["org_id"] for c in MailboxRepository().list_all_connected()}
        assert customer_org in swept
        assert sandbox_org not in swept

    def test_a_sandbox_never_reaches_the_model(self, client, monkeypatch):
        """Anonymous strangers must not be able to spend the deployment's money.

        With drafting switched ON for the whole deployment, a sandbox still
        drafts and verifies with live_llm off — both on the first sync and when
        "Sync" re-proposes something they rejected. A real workspace in the same
        deployment does go live: that is the control that proves the spy works.
        """
        from app.copilot.providers.fake import FakeProvider
        from app.llm import verifier
        from app.saas import sync_service
        from app.saas.sync_service import InboxSyncService

        monkeypatch.setenv("LLM_DRAFTING_ENABLED", "true")
        seen: list[tuple[str, bool]] = []
        real_resolve, real_verify = sync_service.resolve_draft, verifier.verify_draft

        def spy_resolve(*args, **kwargs):
            seen.append(("draft", bool(kwargs.get("live_llm"))))
            return real_resolve(*args, **kwargs)

        def spy_verify(*args, **kwargs):
            seen.append(("verify", bool(kwargs.get("live_llm"))))
            return real_verify(*args, **kwargs)

        monkeypatch.setattr(sync_service, "resolve_draft", spy_resolve)
        monkeypatch.setattr(verifier, "verify_draft", spy_verify)

        # Sandbox: the first sync happens inside open_demo, with live_llm passed
        # explicitly. The hazard is the *second*: an ordinary "Sync" on a
        # deployment with drafting on defaults live_llm from the settings — and
        # a sync that re-proposes something the visitor rejected drafts it
        # again. (A plain re-sync proposes nothing and never reaches the model,
        # which is why rejecting first is the point of this test.)
        open_demo(client)
        org_id = org_of(client)
        first_sync_calls = len(seen)
        assert first_sync_calls, "the spy saw no drafting at all, so this test proves nothing"

        queue = ProposedActionRepository().list_pending_with_messages(org_id, limit=100)["items"]
        reply = next(i for i in queue if i["action"]["action_type"] == "reply")["action"]["id"]
        page = client.get("/app/approvals").text
        client.post(
            f"/app/actions/{reply}/reject", data={"csrf_token": csrf_from(page), "message": ""}
        )
        client.post("/app/sync", data={"csrf_token": csrf_from(client.get("/app/inbox").text)})
        assert len(seen) > first_sync_calls, (
            "the rejected reply was not re-drafted: the test is blind"
        )
        assert not any(live for _kind, live in seen), seen

        # Control: a real workspace in the same deployment does go live.
        seen.clear()
        customer = TestClient(app, follow_redirects=False)
        real_org = sign_up_a_real_customer(customer)
        from app.saas.auth import AuthService

        user = AuthService().resolve(customer.cookies.get(SESSION_COOKIE))
        connection = MailboxRepository().upsert_connection(
            org_id=real_org,
            provider="fake",
            account_email="exec@acmereal.io",
            connected_by=user["id"],
            access_token_enc=None,
            refresh_token_enc=None,
            token_expires_at=None,
            scopes=None,
        )
        InboxSyncService().sync(
            org_id=real_org,
            user_id=user["id"],
            connection_id=connection["id"],
            provider=FakeProvider(),
        )
        assert any(live for _kind, live in seen), "a real workspace should have drafted live"
        assert org_id != real_org

    def test_nothing_is_ever_emailed_to_a_sandbox_owner(self, monkeypatch):
        from app.saas import email

        sender = email.MemorySender()
        monkeypatch.setattr(email, "get_email_sender", lambda: sender)

        email.send_email("demo-abc123@sandbox.invalid", "Reset your password", "…")
        assert sender.outbox == []
        email.send_email("rae@acmereal.io", "Reset your password", "…")
        assert [m.to for m in sender.outbox] == ["rae@acmereal.io"]

    @pytest.mark.parametrize(
        "address, undeliverable",
        [
            ("a@sandbox.invalid", True),
            ("a@invalid", True),
            ("a@Sandbox.INVALID.", True),
            ("a@acme.io", False),
            ("a@invalid.io", False),
            ("a@notinvalid.com", False),
        ],
    )
    def test_undeliverable_means_the_invalid_tld_and_nothing_else(self, address, undeliverable):
        from app.saas.email import is_undeliverable

        assert is_undeliverable(address) is undeliverable


class TestChrome:
    def test_the_banner_says_what_this_is_on_every_page(self, client):
        open_demo(client)
        for path in (
            "/app/inbox",
            "/app/approvals",
            "/app/waiting",
            "/app/activity",
            "/app/settings",
        ):
            html = client.get(path).text
            assert "Demo sandbox" in html, path
            assert "Nothing here is sent anywhere" in html, path
            assert 'href="/signup"' in html, path

    def test_a_real_workspace_has_neither_banner_nor_tour(self):
        customer = TestClient(app, follow_redirects=False)
        sign_up_a_real_customer(customer)
        customer.post(
            "/app/connect/demo", data={"csrf_token": csrf_from(customer.get("/app/connect").text)}
        )
        html = customer.get("/app/inbox?tour=1").text
        assert "Demo sandbox" not in html
        assert "Two minutes in the demo" not in html

    def test_the_tour_links_to_the_drafts_the_verifier_flagged(self, client):
        open_demo(client)
        flagged = [
            item["action"]["id"]
            for item in ProposedActionRepository().list_pending_with_messages(
                org_of(client), limit=100
            )["items"]
            if item["action"].get("verification_status") == "flagged"
        ]
        assert flagged, "the demo mailbox should hold at least one flagged draft"
        inbox = client.get("/app/inbox?tour=1").text
        assert f"/app/approvals#action-{flagged[0]}" in inbox
        # The count is read from the workspace, never typed: it said "one" while
        # the demo held two.
        plural = "s" if len(flagged) != 1 else ""
        assert re.search(rf"{len(flagged)}\s+flagged\s+draft{plural}\b", inbox), (
            len(flagged),
            plural,
        )
        # ...and that anchor exists on the page it points at.
        assert f'id="action-{flagged[0]}"' in client.get("/app/approvals").text

    def test_the_tour_is_opt_in_after_the_first_visit(self, client):
        open_demo(client)
        assert "Two minutes in the demo" not in client.get("/app/inbox").text
        assert "Two minutes in the demo" in client.get("/app/inbox?tour=1").text


class TestSettingsInASandbox:
    """A button that can only answer 403 is a worse demo than no button."""

    REFUSED = (
        "/app/settings/password",
        "/app/settings/license",
        "/app/settings/escalation",
        "/app/members/invite",
        "/app/settings/delete-org",
        "/app/settings/export",
    )

    def test_it_does_not_offer_what_the_server_would_refuse(self, client):
        open_demo(client)
        html = client.get("/app/settings").text
        for action in self.REFUSED:
            assert action not in html, f"a sandbox visitor is offered {action}, which always 403s"
        assert "demo sandbox" in html.lower()
        # ...but the read-only facts are still there to look at.
        assert "Escalation contacts" in html
        assert "legal@northwind.example" in html

    def test_a_real_owner_still_gets_every_control(self):
        """The control: hiding them for the sandbox must not hide them for customers."""
        customer = TestClient(app, follow_redirects=False)
        sign_up_a_real_customer(customer)
        html = customer.get("/app/settings").text
        for action in self.REFUSED:
            assert action in html, f"a real owner lost {action}"
        assert "demo sandbox" not in html.lower()


class TestOperatorIsNotFlooded:
    """Every click on the public demo adds an organization.

    The operator console lists workspaces newest-first, so without this a week of
    visitors puts two hundred identical "Northwind Industries" rows above the
    customers the page exists to show, and turns the mailbox and member counts
    into noise.
    """

    TOKEN = "demo-test-operator-token"

    @pytest.fixture(autouse=True)
    def _operator(self, monkeypatch):
        monkeypatch.setenv("OPERATOR_TOKEN", self.TOKEN)

    def _api(self, client):
        return client.get(
            "/operator/orgs", headers={"Authorization": f"Bearer {self.TOKEN}"}
        ).json()

    def test_a_sandbox_is_not_listed_as_a_customer(self, client):
        customer = TestClient(app, follow_redirects=False)
        customer_org = sign_up_a_real_customer(customer)
        open_demo(client)
        sandbox_org = org_of(client)

        listed = {o["organization"]["id"] for o in self._api(client)["organizations"]}
        assert customer_org in listed
        assert sandbox_org not in listed
        # ...but it can be asked for, and it is counted.
        assert sandbox_org in {o["id"] for o in ORGS.list_all(include_sandboxes=True)}
        assert self._api(client)["demo_sandboxes_live"] == 1

    def test_the_health_figures_leave_the_demo_out(self, client):
        from types import SimpleNamespace

        from app.saas.operator_views import build_health

        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
        before = build_health(request)
        open_demo(client)
        after = build_health(request)

        assert after["totals"]["workspaces"] == before["totals"]["workspaces"]
        assert after["totals"]["members"] == before["totals"]["members"]
        assert after["connections"].get("connected", 0) == before["connections"].get("connected", 0)
        assert after["sandboxes"] == before["sandboxes"] + 1

    def test_the_page_says_how_many_visitors_are_inside_one(self, client):
        open_demo(client)
        operator = TestClient(app, follow_redirects=False)
        assert (
            operator.post("/operator/session", data={"operator_token": self.TOKEN}).status_code
            == 303
        )
        html = operator.get("/operator").text
        assert "plus 1 live demo sandbox" in html
