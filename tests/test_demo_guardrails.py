"""The shared demo login: when it is advertised, and what it may not do.

A public demo deployment (DEMO_LOGIN_ENABLED=true) hands the demo session to
anyone on the internet, so the account must be able to run the demo — and
nothing else. These tests pin the prefill matrix and the guardrails.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.saas.demo_seed import DEMO_ORG_NAME, DEMO_OWNER_EMAIL, DEMO_OWNER_PASSWORD

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def csrf_from(html: str) -> str:
    match = CSRF_RE.search(html)
    assert match
    return match.group(1)


@pytest.fixture
def client():
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def demo_account():
    """Ensure the demo owner exists (without the slow mailbox sync)."""
    from app.core.db import migrate_db
    from app.saas.provisioning import provision_org
    from app.saas.repository import UserRepository

    migrate_db()
    users = UserRepository()
    if not users.get_by_email_global(DEMO_OWNER_EMAIL):
        provision_org(
            org_name=DEMO_ORG_NAME,
            owner_email=DEMO_OWNER_EMAIL,
            owner_name="Alex Chen",
            password=DEMO_OWNER_PASSWORD,
        )
    return DEMO_OWNER_EMAIL


class TestPrefillMatrix:
    def _login_html(self, client) -> str:
        return client.get("/login").text

    def test_prefilled_in_development_by_default(self, client, demo_account, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "development")
        monkeypatch.delenv("DEMO_LOGIN_ENABLED", raising=False)
        html = self._login_html(client)
        assert DEMO_OWNER_EMAIL in html
        assert DEMO_OWNER_PASSWORD in html

    def test_hidden_in_production_by_default(self, client, demo_account, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("AUTH_SECRET_KEY", "a-long-random-production-secret")
        monkeypatch.delenv("DEMO_LOGIN_ENABLED", raising=False)
        html = self._login_html(client)
        assert DEMO_OWNER_PASSWORD not in html

    def test_production_can_opt_in_explicitly(self, client, demo_account, monkeypatch):
        """The public demo deployment: hardened config AND the prefilled login."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("AUTH_SECRET_KEY", "a-long-random-production-secret")
        monkeypatch.setenv("DEMO_LOGIN_ENABLED", "true")
        html = self._login_html(client)
        assert DEMO_OWNER_EMAIL in html
        assert DEMO_OWNER_PASSWORD in html

    def test_development_can_opt_out(self, client, demo_account, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "development")
        monkeypatch.setenv("DEMO_LOGIN_ENABLED", "false")
        html = self._login_html(client)
        assert DEMO_OWNER_PASSWORD not in html


class TestDemoAccountGuardrails:
    def _sign_in_as_demo(self, client) -> None:
        page = client.get("/login").text
        response = client.post(
            "/login",
            data={
                "csrf_token": csrf_from(page),
                "email": DEMO_OWNER_EMAIL,
                "password": DEMO_OWNER_PASSWORD,
            },
        )
        assert response.status_code == 303

    def test_demo_owner_cannot_change_the_password(self, client, demo_account, monkeypatch):
        """The password IS the demo — changing it locks out the next sales call."""
        monkeypatch.setenv("DEMO_LOGIN_ENABLED", "true")
        self._sign_in_as_demo(client)
        page = client.get("/app/settings").text
        response = client.post(
            "/app/settings/password",
            data={
                "csrf_token": csrf_from(page),
                "current_password": DEMO_OWNER_PASSWORD,
                "new_password": "hijacked-by-a-visitor",
            },
        )
        assert response.status_code == 403
        assert "demo account" in response.text

    def test_demo_owner_cannot_delete_the_workspace(self, client, demo_account, monkeypatch):
        monkeypatch.setenv("DEMO_LOGIN_ENABLED", "true")
        self._sign_in_as_demo(client)
        page = client.get("/app/settings").text
        response = client.post(
            "/app/settings/delete-org",
            data={"csrf_token": csrf_from(page), "confirm": "northwind-industries"},
        )
        assert response.status_code == 403

    def test_demo_owner_cannot_invite_members(self, client, demo_account, monkeypatch):
        monkeypatch.setenv("DEMO_LOGIN_ENABLED", "true")
        self._sign_in_as_demo(client)
        page = client.get("/app/settings").text
        response = client.post(
            "/app/members/invite",
            data={
                "csrf_token": csrf_from(page),
                "email": "attacker@evil.example",
                "full_name": "Not Welcome",
                "role": "admin",
            },
        )
        assert response.status_code == 403

    def test_demo_owner_cannot_export_the_workspace(self, client, demo_account, monkeypatch):
        monkeypatch.setenv("DEMO_LOGIN_ENABLED", "true")
        self._sign_in_as_demo(client)
        assert client.get("/app/settings/export").status_code == 403

    def test_api_surface_is_equally_guarded(self, client, demo_account, monkeypatch):
        monkeypatch.setenv("DEMO_LOGIN_ENABLED", "true")
        token = client.post(
            "/auth/login",
            json={"email": DEMO_OWNER_EMAIL, "password": DEMO_OWNER_PASSWORD},
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        assert (
            client.post(
                "/auth/change-password",
                json={"current_password": DEMO_OWNER_PASSWORD, "new_password": "hijacked-8chars"},
                headers=headers,
            ).status_code
            == 403
        )
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

    def test_guardrails_are_inert_when_demo_login_is_off(self, client, demo_account, monkeypatch):
        """A private deployment reusing the demo email is not restricted.

        Proven with a wrong current password: the request gets past the demo
        guard (which would 403) and fails on credentials (400) instead."""
        monkeypatch.setenv("DEMO_LOGIN_ENABLED", "false")
        self._sign_in_as_demo(client)
        page = client.get("/app/settings").text
        response = client.post(
            "/app/settings/password",
            data={
                "csrf_token": csrf_from(page),
                "current_password": "not-the-real-password",
                "new_password": "irrelevant-8chars",
            },
        )
        assert response.status_code == 400


class TestSeedWorksWithSignupDisabled:
    def test_provisioning_bypasses_the_signup_gate(self, monkeypatch):
        """Production runs SIGNUP_ENABLED=false; the seeder must still work.

        Exercises provision_org directly (the path seed_demo uses) rather than
        the full seed, which would re-sync the demo mailbox on every test run."""
        import uuid

        from app.saas.auth import AuthError, AuthService
        from app.saas.data_lifecycle import DataLifecycleService
        from app.saas.provisioning import provision_org

        monkeypatch.setenv("SIGNUP_ENABLED", "false")

        email = f"seed-{uuid.uuid4().hex[:10]}@example.com"
        with pytest.raises(AuthError):
            AuthService().signup(
                email=email, password="password123", full_name="X", org_name="X Co"
            )
        result = provision_org(org_name="X Co", owner_email=email, owner_name="X")
        assert result["owner"]["email"] == email
        assert result["temp_password"]
        assert result["entitlement"]["plan"] == "trial"
        DataLifecycleService().delete_org(result["organization"]["id"])
