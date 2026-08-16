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
    match = re.search(r"reset_token=([^\s]+)", body)
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
