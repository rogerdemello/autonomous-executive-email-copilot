"""Tests for password reset and invite email (Track B)."""

from __future__ import annotations

import re
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.saas import email as email_mod
from app.saas.email import MemorySender


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def outbox(monkeypatch):
    sender = MemorySender()
    monkeypatch.setattr(email_mod, "get_email_sender", lambda: sender)
    return sender


def _email() -> str:
    return f"user_{uuid.uuid4().hex[:12]}@example.com"


def _signup(client, password="hunter2pass"):
    email = _email()
    resp = client.post(
        "/auth/signup",
        json={"email": email, "password": password, "full_name": "U", "org_name": "Acme"},
    )
    assert resp.status_code == 200, resp.text
    return email, password, resp.json()


def _token_from(body: str) -> str:
    match = re.search(r"[?&]token=([^\s]+)", body)
    assert match, f"no token in email body: {body}"
    return match.group(1)


class TestPasswordReset:
    def test_full_reset_flow(self, client, outbox):
        email, old_pw, _ = _signup(client)

        # Request reset -> 200 and an email with a link is queued.
        resp = client.post("/auth/forgot-password", json={"email": email})
        assert resp.status_code == 200
        assert len(outbox.outbox) == 1
        assert outbox.outbox[0].to == email
        token = _token_from(outbox.outbox[0].body)

        # Reset the password.
        r = client.post("/auth/reset-password", json={"token": token, "new_password": "newpass123"})
        assert r.status_code == 200

        # Old password no longer works; new one does.
        assert (
            client.post("/auth/login", json={"email": email, "password": old_pw}).status_code == 401
        )
        assert (
            client.post("/auth/login", json={"email": email, "password": "newpass123"}).status_code
            == 200
        )

    def test_unknown_email_is_silent(self, client, outbox):
        resp = client.post("/auth/forgot-password", json={"email": "nobody@nowhere.example"})
        assert resp.status_code == 200  # never reveals existence
        assert outbox.outbox == []  # ...and sends nothing

    def test_invalid_token_rejected(self, client):
        resp = client.post(
            "/auth/reset-password", json={"token": "not-a-real-token", "new_password": "whatever12"}
        )
        assert resp.status_code == 400

    def test_session_token_is_not_a_reset_token(self, client, outbox):
        _e, _p, data = _signup(client)
        session = data["access_token"]
        resp = client.post(
            "/auth/reset-password", json={"token": session, "new_password": "whatever12"}
        )
        assert resp.status_code == 400  # typ guard

    def test_reset_endpoints_bypass_operator_token(self, client, outbox, monkeypatch):
        # Even with an operator API_AUTH_TOKEN set, the public reset endpoints work.
        monkeypatch.setenv("API_AUTH_TOKEN", "operator-secret")
        email, _pw, _ = _signup(client)
        assert client.post("/auth/forgot-password", json={"email": email}).status_code == 200


class TestInviteEmail:
    def test_invite_sends_email(self, client, outbox):
        _e, _p, owner = _signup(client)
        member_email = _email()
        resp = client.post(
            "/org/members",
            headers={"Authorization": f"Bearer {owner['access_token']}"},
            json={
                "email": member_email,
                "full_name": "M",
                "role": "member",
                "temp_password": "temppass12",
            },
        )
        assert resp.status_code == 200, resp.text
        assert any(m.to == member_email and "temppass12" in m.body for m in outbox.outbox)


CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


class TestWebResetFlow:
    """The browser flow: /forgot-password form -> emailed link -> /reset-password."""

    def test_full_web_reset_flow(self, client, outbox):
        email, old_pw, _ = _signup(client)

        page = client.get("/forgot-password")
        assert page.status_code == 200
        csrf = CSRF_RE.search(page.text).group(1)

        resp = client.post("/forgot-password", data={"email": email, "csrf_token": csrf})
        assert resp.status_code == 200
        assert "reset link is on its way" in resp.text
        assert len(outbox.outbox) == 1
        link = re.search(r"https?://\S+/reset-password\?token=\S+", outbox.outbox[0].body)
        assert link, f"no web reset link in email body: {outbox.outbox[0].body}"
        token = _token_from(outbox.outbox[0].body)

        # The emailed link renders the form (not a 404, not a dashboard).
        form = client.get("/reset-password", params={"token": token})
        assert form.status_code == 200
        assert 'name="token"' in form.text
        csrf = CSRF_RE.search(form.text).group(1)

        resp = client.post(
            "/reset-password",
            data={"token": token, "password": "brand-new-pass1", "csrf_token": csrf},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/login?notice=reset"

        assert (
            client.post("/auth/login", json={"email": email, "password": old_pw}).status_code == 401
        )
        assert (
            client.post(
                "/auth/login", json={"email": email, "password": "brand-new-pass1"}
            ).status_code
            == 200
        )

    def test_unknown_email_renders_same_confirmation(self, client, outbox):
        page = client.get("/forgot-password")
        csrf = CSRF_RE.search(page.text).group(1)
        resp = client.post(
            "/forgot-password",
            data={"email": "nobody@nowhere.example", "csrf_token": csrf},
        )
        assert resp.status_code == 200
        assert "reset link is on its way" in resp.text  # indistinguishable from a hit
        assert outbox.outbox == []

    def test_reset_page_without_token_redirects_to_request_form(self, client):
        resp = client.get("/reset-password", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/forgot-password"

    def test_bad_token_shows_error_not_crash(self, client):
        form = client.get("/reset-password", params={"token": "junk"})
        csrf = CSRF_RE.search(form.text).group(1)
        resp = client.post(
            "/reset-password",
            data={"token": "junk", "password": "long-enough-pw1", "csrf_token": csrf},
        )
        assert resp.status_code == 400
        assert "invalid or has expired" in resp.text

    def test_login_page_links_to_forgot_password(self, client):
        assert 'href="/forgot-password"' in client.get("/login").text


class TestTokenTypeConfusion:
    """Only a token minted as a session may open one.

    Every token this app signs shares one secret; a reset token also carries
    sub+org and used to be accepted by the session resolver — turning any
    leaked reset *link* into a full sign-in.
    """

    def test_reset_token_is_not_a_session(self, client, outbox):
        email, _, _ = _signup(client)
        client.post("/auth/forgot-password", json={"email": email})
        reset_token = _token_from(outbox.outbox[0].body)

        resp = client.get("/auth/me", headers={"Authorization": f"Bearer {reset_token}"})
        assert resp.status_code == 401

    def test_oauth_state_token_is_not_a_session(self, client):
        """The OAuth state is handed to the identity provider in a URL query
        string; it must never double as a bearer credential."""
        from app.saas import oauth

        _, _, data = _signup(client)
        me = client.get(
            "/auth/me", headers={"Authorization": f"Bearer {data['access_token']}"}
        ).json()
        state = oauth.sign_state(
            org_id=me["user"]["org_id"], user_id=me["user"]["id"], provider="google"
        )
        resp = client.get("/auth/me", headers={"Authorization": f"Bearer {state}"})
        assert resp.status_code == 401

    def test_real_session_still_works(self, client):
        _, _, data = _signup(client)
        resp = client.get("/auth/me", headers={"Authorization": f"Bearer {data['access_token']}"})
        assert resp.status_code == 200
