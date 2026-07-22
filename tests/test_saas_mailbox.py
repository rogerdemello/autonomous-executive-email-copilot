"""Tests for the mailbox layer: token vault, OAuth flow, and connect/disconnect."""

from __future__ import annotations

import base64
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from env.api import app
from env.saas import oauth
from env.saas.crypto import DecryptionError, TokenVault


@pytest.fixture
def client():
    return TestClient(app)


def _email() -> str:
    return f"admin_{uuid.uuid4().hex[:12]}@example.com"


def _signup(client):
    email = _email()
    resp = client.post(
        "/auth/signup",
        json={"email": email, "password": "hunter2pass", "full_name": "Admin", "org_name": "Acme"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _hdr(token):
    return {"Authorization": f"Bearer {token}"}


class TestTokenVault:
    def test_roundtrip(self):
        v = TokenVault("a-secret")
        blob = v.encrypt("ya29.super-secret-refresh-token")
        assert blob != "ya29.super-secret-refresh-token"
        assert v.decrypt(blob) == "ya29.super-secret-refresh-token"

    def test_wrong_key_fails_authentication(self):
        blob = TokenVault("key-a").encrypt("secret")
        with pytest.raises(DecryptionError):
            TokenVault("key-b").decrypt(blob)

    def test_tampered_ciphertext_rejected(self):
        v = TokenVault("k")
        blob = v.encrypt("secret-value")
        version, _, b64 = blob.partition("$")
        raw = bytearray(base64.b64decode(b64))
        raw[-1] ^= 0x01  # flip a ciphertext bit
        tampered = f"{version}${base64.b64encode(bytes(raw)).decode()}"
        with pytest.raises(DecryptionError):
            v.decrypt(tampered)

    @pytest.mark.parametrize("bad", ["", "v1$", "garbage", "v2$abcd"])
    def test_malformed_rejected(self, bad):
        with pytest.raises(DecryptionError):
            TokenVault("k").decrypt(bad)


class TestOAuthPure:
    def test_state_sign_verify_roundtrip(self):
        state = oauth.sign_state(org_id="o1", user_id="u1", provider="google")
        claims = oauth.verify_state(state)
        assert claims["org"] == "o1"
        assert claims["sub"] == "u1"
        assert claims["prov"] == "google"

    def test_session_token_is_not_state(self):
        from env.saas import tokens

        session = tokens.encode(
            {"sub": "u"}, oauth.get_settings().resolved_auth_secret, ttl_seconds=60
        )
        with pytest.raises(tokens.TokenError):
            oauth.verify_state(session)

    def test_build_authorize_url(self):
        prov = oauth.get_provider("google")
        url = oauth.build_authorize_url(
            prov, client_id="cid", redirect="https://app/cb", state="st"
        )
        assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
        assert "client_id=cid" in url
        assert "state=st" in url
        assert "gmail.readonly" in url
        assert "access_type=offline" in url

    def test_provider_availability(self, monkeypatch):
        assert not oauth.provider_available("google")  # no creds by default
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csecret")
        assert oauth.provider_available("google")


class TestConnectFlow:
    def test_connect_requires_configured_provider(self, client):
        data = _signup(client)
        resp = client.post("/mailbox/connect/google", headers=_hdr(data["access_token"]))
        assert resp.status_code == 400  # provider not configured

    def test_connect_returns_authorize_url_when_configured(self, client, monkeypatch):
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csecret")
        monkeypatch.setenv("OAUTH_REDIRECT_BASE_URL", "https://app.example.com")
        data = _signup(client)
        resp = client.post("/mailbox/connect/google", headers=_hdr(data["access_token"]))
        assert resp.status_code == 200, resp.text
        assert resp.json()["authorize_url"].startswith("https://accounts.google.com/")

    def test_member_cannot_connect(self, client, monkeypatch):
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csecret")
        owner = _signup(client)
        member_email = _email()
        client.post(
            "/org/members",
            headers=_hdr(owner["access_token"]),
            json={
                "email": member_email,
                "full_name": "M",
                "role": "member",
                "temp_password": "temppass12",
            },
        )
        member_token = client.post(
            "/auth/login", json={"email": member_email, "password": "temppass12"}
        ).json()["access_token"]
        resp = client.post("/mailbox/connect/google", headers=_hdr(member_token))
        assert resp.status_code == 403

    def test_full_callback_persists_encrypted_connection(self, client, monkeypatch):
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csecret")
        monkeypatch.setenv("OAUTH_REDIRECT_BASE_URL", "https://app.example.com")
        data = _signup(client)
        org_id = data["organization"]["id"]
        user_id = data["user"]["id"]

        # Build a fake id_token carrying the account email.
        payload = (
            base64.urlsafe_b64encode(json.dumps({"email": "ceo@acme.com"}).encode())
            .decode()
            .rstrip("=")
        )
        fake_id_token = f"h.{payload}.s"

        def fake_exchange(provider, *, code, client_id, client_secret, redirect):
            assert code == "auth-code-123"
            return {
                "access_token": "access-abc",
                "refresh_token": "refresh-xyz",
                "expires_in": 3600,
                "id_token": fake_id_token,
            }

        monkeypatch.setattr(oauth, "exchange_code", fake_exchange)

        state = oauth.sign_state(org_id=org_id, user_id=user_id, provider="google")
        resp = client.get(
            "/mailbox/oauth/callback",
            params={"code": "auth-code-123", "state": state},
            follow_redirects=False,
        )
        assert resp.status_code == 200
        assert "ceo@acme.com" in resp.text

        # The connection should now be listed for the org, without token material.
        conns = client.get("/mailbox/connections", headers=_hdr(data["access_token"])).json()[
            "connections"
        ]
        assert len(conns) == 1
        conn = conns[0]
        assert conn["account_email"] == "ceo@acme.com"
        assert conn["provider"] == "google"
        assert conn["status"] == "connected"
        assert "access_token_enc" not in conn and "refresh_token_enc" not in conn

        # Tokens are stored encrypted, not in plaintext.
        from env.db import get_session
        from env.saas.models_db import MailboxConnection

        with get_session() as session:
            row = (
                session.query(MailboxConnection).filter(MailboxConnection.id == conn["id"]).first()
            )
            assert row.access_token_enc and "access-abc" not in row.access_token_enc
            assert row.refresh_token_enc and "refresh-xyz" not in row.refresh_token_enc
            from env.saas.crypto import get_vault

            assert get_vault().decrypt(row.access_token_enc) == "access-abc"

        # Disconnect removes it.
        dresp = client.delete(
            f"/mailbox/connections/{conn['id']}", headers=_hdr(data["access_token"])
        )
        assert dresp.status_code == 200
        after = client.get("/mailbox/connections", headers=_hdr(data["access_token"])).json()[
            "connections"
        ]
        assert after == []

    def test_callback_rejects_bad_state(self, client):
        resp = client.get(
            "/mailbox/oauth/callback",
            params={"code": "x", "state": "not-a-valid-state"},
            follow_redirects=False,
        )
        assert resp.status_code == 400

    def test_callback_tenant_binding(self, client, monkeypatch):
        # A connection lands in the org named by the signed state, not any other.
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csecret")
        org1 = _signup(client)
        org2 = _signup(client)

        def fake_exchange(provider, *, code, client_id, client_secret, redirect):
            return {"access_token": "a", "expires_in": 3600, "id_token": ""}

        monkeypatch.setattr(oauth, "exchange_code", fake_exchange)
        state = oauth.sign_state(
            org_id=org1["organization"]["id"], user_id=org1["user"]["id"], provider="google"
        )
        client.get("/mailbox/oauth/callback", params={"code": "c", "state": state})

        c1 = client.get("/mailbox/connections", headers=_hdr(org1["access_token"])).json()[
            "connections"
        ]
        c2 = client.get("/mailbox/connections", headers=_hdr(org2["access_token"])).json()[
            "connections"
        ]
        assert len(c1) == 1
        assert c2 == []
