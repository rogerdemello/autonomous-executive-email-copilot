"""OIDC SSO tests — full flow with a locally-generated RSA key (no browser/IdP).

We stand up a fake IdP entirely in-process: generate an RSA keypair, publish its
public half as a JWKS, sign a real RS256 id_token, and patch the OIDC module's
discovery/token/JWKS network calls to serve them. This exercises signature
verification, claim checks, provisioning, and session issuance offline.
"""

from __future__ import annotations

import base64
import json
import time
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.main import app
from app.saas import auth as auth_mod
from app.saas import oidc

ISSUER = "https://idp.example.com"
CLIENT_ID = "test-client-id"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


@pytest.fixture
def idp():
    """A fake IdP: RSA key, JWKS, and an id_token signer."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = key.public_key().public_numbers()
    n = _b64url(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big"))
    e = _b64url(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big"))
    jwks = {"keys": [{"kty": "RSA", "kid": "k1", "alg": "RS256", "use": "sig", "n": n, "e": e}]}

    def sign(claims: dict, *, kid: str = "k1") -> str:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        header = _b64url(json.dumps({"alg": "RS256", "kid": kid}).encode())
        payload = _b64url(json.dumps(claims).encode())
        signing_input = f"{header}.{payload}".encode("ascii")
        sig = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        return f"{header}.{payload}.{_b64url(sig)}"

    return type("IdP", (), {"jwks": jwks, "sign": staticmethod(sign)})


def _configure_sso(monkeypatch):
    monkeypatch.setenv("OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("OIDC_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "shh")
    monkeypatch.setenv("APP_PUBLIC_URL", "https://app.example.com")


def _patch_network(monkeypatch, idp, *, nonce=None, id_token_claims_extra=None):
    """Patch the three network seams. ``nonce`` is what the fake IdP echoes back
    in the id_token — pass the same value used for ``sign_state`` for the happy
    path, or a different one to exercise the nonce-binding rejection."""
    config = {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "jwks_uri": f"{ISSUER}/jwks",
    }
    monkeypatch.setattr(oidc, "fetch_discovery", lambda issuer: config)
    monkeypatch.setattr(oidc, "fetch_jwks", lambda cfg: idp.jwks)

    def fake_exchange(*, config, code, client_id, client_secret, redirect):
        claims = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": "idp-user-1",
            "email": f"user_{uuid.uuid4().hex[:10]}@corp.example",
            "email_verified": True,
            "name": "SSO User",
            "exp": int(time.time()) + 300,
            "nonce": nonce,
        }
        if id_token_claims_extra:
            claims.update(id_token_claims_extra)
        return {"id_token": idp.sign(claims), "access_token": "a"}

    monkeypatch.setattr(oidc, "exchange_code", fake_exchange)


class TestOIDCVerification:
    def test_signature_and_claims_roundtrip(self, idp):
        claims = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": "s1",
            "email": "a@corp.example",
            "email_verified": True,
            "name": "A",
            "exp": int(time.time()) + 300,
            "nonce": "n1",
        }
        identity = oidc.verify_id_token(
            idp.sign(claims), jwks=idp.jwks, issuer=ISSUER, audience=CLIENT_ID, nonce="n1"
        )
        assert identity.email == "a@corp.example"
        assert identity.subject == "s1"

    def test_tampered_signature_rejected(self, idp):
        claims = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "email": "a@corp.example",
            "exp": int(time.time()) + 300,
        }
        token = idp.sign(claims)
        head, payload, _sig = token.split(".")
        forged = f"{head}.{payload}.{_b64url(b'not-a-real-signature')}"
        with pytest.raises(oidc.OIDCError):
            oidc.verify_id_token(forged, jwks=idp.jwks, issuer=ISSUER, audience=CLIENT_ID)

    def test_wrong_audience_rejected(self, idp):
        claims = {
            "iss": ISSUER,
            "aud": "someone-else",
            "email": "a@corp.example",
            "exp": int(time.time()) + 300,
        }
        with pytest.raises(oidc.OIDCError):
            oidc.verify_id_token(idp.sign(claims), jwks=idp.jwks, issuer=ISSUER, audience=CLIENT_ID)

    def test_expired_rejected(self, idp):
        claims = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "email": "a@corp.example",
            "exp": int(time.time()) - 10,
        }
        with pytest.raises(oidc.OIDCError):
            oidc.verify_id_token(idp.sign(claims), jwks=idp.jwks, issuer=ISSUER, audience=CLIENT_ID)


class TestSSOFlow:
    def test_status_reflects_config(self, monkeypatch):
        client = TestClient(app)
        assert client.get("/auth/sso/status").json()["enabled"] is False
        _configure_sso(monkeypatch)
        assert client.get("/auth/sso/status").json()["enabled"] is True

    def test_login_redirects_to_idp(self, monkeypatch, idp):
        _configure_sso(monkeypatch)
        _patch_network(monkeypatch, idp)
        client = TestClient(app)
        resp = client.get("/auth/sso/login", follow_redirects=False)
        assert resp.status_code == 307
        assert resp.headers["location"].startswith(f"{ISSUER}/authorize?")
        assert "openid" in resp.headers["location"]

    def test_full_callback_provisions_and_issues_session(self, monkeypatch, idp):
        _configure_sso(monkeypatch)
        nonce = "nonce-happy-path"
        _patch_network(monkeypatch, idp, nonce=nonce)
        client = TestClient(app)

        # A valid signed state carrying the nonce the fake token will echo.
        state = oidc.sign_state(nonce=nonce)
        resp = client.get(
            "/auth/sso/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )
        # The browser lands signed in: session travels as an HttpOnly cookie,
        # never as a token in the redirect URL (history/referrer leakage).
        assert resp.status_code == 303
        assert resp.headers["location"] == "/app/inbox"
        assert "sso_token" not in resp.headers["location"]
        from app.saas.deps import SESSION_COOKIE

        token = resp.cookies.get(SESSION_COOKIE)
        assert token

        # The cookie's session token works against an authenticated endpoint.
        me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json()["user"]["role"] == "owner"  # provisioned as org owner

    def test_callback_rejects_bad_state(self, monkeypatch, idp):
        _configure_sso(monkeypatch)
        _patch_network(monkeypatch, idp)
        client = TestClient(app)
        resp = client.get(
            "/auth/sso/callback",
            params={"code": "x", "state": "not-a-valid-state"},
            follow_redirects=False,
        )
        assert resp.status_code == 401

    def test_callback_rejects_replayed_nonce(self, monkeypatch, idp):
        """The id_token's nonce must match the one bound into the signed state.

        This is the anti-replay binding: an id_token minted for a *different*
        authorize request must not be accepted against this state.
        """
        _configure_sso(monkeypatch)
        _patch_network(monkeypatch, idp, nonce="nonce-from-another-request")
        client = TestClient(app)
        state = oidc.sign_state(nonce="nonce-for-this-request")
        resp = client.get(
            "/auth/sso/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )
        assert resp.status_code == 401
        assert "nonce" in resp.json()["detail"]

    def test_existing_user_logs_in_without_new_org(self, monkeypatch, idp):
        _configure_sso(monkeypatch)
        # First, create a normal account.
        client = TestClient(app)
        email = f"existing_{uuid.uuid4().hex[:8]}@corp.example"
        client.post(
            "/auth/signup",
            json={"email": email, "password": "hunter2pass", "full_name": "X", "org_name": "Corp"},
        )
        # SSO returns that same email -> should log into the existing account.
        svc = auth_mod.AuthService()
        user = svc.login_or_provision_sso(email=email, full_name="X")
        assert user["email"] == email
        assert "password_hash" not in user
