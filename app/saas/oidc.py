"""OpenID Connect (OIDC) single sign-on.

Implements the Authorization Code flow against any standards-compliant OIDC
provider (Okta, Entra ID, Google, Auth0, Keycloak, ...): discovery, the
authorize redirect with a signed CSRF+nonce state, code exchange, and — the part
that actually establishes trust — **RS256 id_token signature verification against
the issuer's JWKS**, plus issuer/audience/expiry/nonce claim checks.

Signature verification uses ``cryptography`` directly (no PyJWT dependency). All
network calls (discovery, token, JWKS) go through small functions that are
trivially patched in tests, so the whole flow is exercised offline by signing a
test token with a locally-generated RSA key.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass

from app.core.config import get_settings

from . import tokens

_STATE_TTL_SECONDS = 900


class OIDCError(Exception):
    """Raised when SSO configuration, network, or token validation fails."""


@dataclass(frozen=True)
class OIDCIdentity:
    """The verified identity asserted by the IdP."""

    subject: str
    email: str
    name: str
    email_verified: bool


def _b64url_decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


# --------------------------------------------------------------------------- #
# Configuration / state (pure)
# --------------------------------------------------------------------------- #
def redirect_uri(request_base_url: str | None = None) -> str:
    settings = get_settings()
    base = (settings.app_public_url or request_base_url or "").rstrip("/")
    return f"{base}/auth/sso/callback"


def sign_state(*, nonce: str, now: float | None = None) -> str:
    """Signed, expiring CSRF state that also binds the id_token nonce."""
    return tokens.encode(
        {"typ": "oidc_state", "nonce": nonce},
        get_settings().resolved_auth_secret,
        ttl_seconds=_STATE_TTL_SECONDS,
        now=now,
    )


def verify_state(state: str, *, now: float | None = None) -> dict:
    # tokens.decode raises TokenError for a forged/expired/garbled state. Callers
    # handle OIDCError only, so translate it here — otherwise a tampered state
    # escapes as an unhandled 500 instead of the 401 it is.
    try:
        claims = tokens.decode(state, get_settings().resolved_auth_secret, now=now)
    except tokens.TokenError as exc:
        raise OIDCError(f"invalid SSO state token: {exc}") from exc
    if claims.get("typ") != "oidc_state":
        raise OIDCError("invalid SSO state token")
    return claims


def build_authorize_url(
    *, config: dict, client_id: str, redirect: str, state: str, nonce: str
) -> str:
    from urllib.parse import urlencode

    params = {
        "client_id": client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
    }
    return f"{config['authorization_endpoint']}?{urlencode(params)}"


# --------------------------------------------------------------------------- #
# Network (patched in tests)
# --------------------------------------------------------------------------- #
def fetch_discovery(issuer: str) -> dict:
    """Fetch the provider's OpenID configuration document."""
    import httpx

    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    try:
        resp = httpx.get(url, timeout=15.0)
    except httpx.HTTPError as exc:  # pragma: no cover - network path
        raise OIDCError(f"OIDC discovery unreachable: {exc}") from exc
    if resp.status_code >= 400:
        raise OIDCError(f"OIDC discovery failed ({resp.status_code})")
    return resp.json()


def exchange_code(
    *, config: dict, code: str, client_id: str, client_secret: str, redirect: str
) -> dict:
    import httpx

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    try:
        resp = httpx.post(config["token_endpoint"], data=data, timeout=15.0)
    except httpx.HTTPError as exc:  # pragma: no cover - network path
        raise OIDCError(f"OIDC token endpoint unreachable: {exc}") from exc
    if resp.status_code >= 400:
        raise OIDCError(f"OIDC token exchange failed ({resp.status_code}): {resp.text[:200]}")
    return resp.json()


def fetch_jwks(config: dict) -> dict:
    import httpx

    try:
        resp = httpx.get(config["jwks_uri"], timeout=15.0)
    except httpx.HTTPError as exc:  # pragma: no cover - network path
        raise OIDCError(f"JWKS unreachable: {exc}") from exc
    if resp.status_code >= 400:
        raise OIDCError(f"JWKS fetch failed ({resp.status_code})")
    return resp.json()


# --------------------------------------------------------------------------- #
# id_token verification (pure, given the JWKS)
# --------------------------------------------------------------------------- #
def _rsa_key_from_jwk(jwk: dict):
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers

    n = int.from_bytes(_b64url_decode(jwk["n"]), "big")
    e = int.from_bytes(_b64url_decode(jwk["e"]), "big")
    return RSAPublicNumbers(e, n).public_key()


def verify_id_token(
    id_token: str,
    *,
    jwks: dict,
    issuer: str,
    audience: str,
    nonce: str | None = None,
    now: float | None = None,
) -> OIDCIdentity:
    """Verify an id_token's RS256 signature (against JWKS) and its claims."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    try:
        header_b64, payload_b64, sig_b64 = id_token.split(".")
    except ValueError as exc:
        raise OIDCError("malformed id_token") from exc

    try:
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
        signature = _b64url_decode(sig_b64)
    except (ValueError, json.JSONDecodeError) as exc:
        raise OIDCError("undecodable id_token") from exc

    if header.get("alg") != "RS256":
        raise OIDCError(f"unsupported id_token alg: {header.get('alg')!r}")

    kid = header.get("kid")
    keys = jwks.get("keys", [])
    jwk = next((k for k in keys if k.get("kid") == kid), None) or (keys[0] if keys else None)
    if jwk is None:
        raise OIDCError("no matching JWKS key for id_token")

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    try:
        _rsa_key_from_jwk(jwk).verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as exc:
        raise OIDCError("id_token signature verification failed") from exc

    # Claim validation.
    if payload.get("iss") != issuer:
        raise OIDCError("id_token issuer mismatch")
    aud = payload.get("aud")
    aud_ok = audience == aud or (isinstance(aud, list) and audience in aud)
    if not aud_ok:
        raise OIDCError("id_token audience mismatch")
    current = time.time() if now is None else now
    if not isinstance(payload.get("exp"), (int, float)) or current >= payload["exp"]:
        raise OIDCError("id_token has expired")
    if nonce is not None and payload.get("nonce") != nonce:
        raise OIDCError("id_token nonce mismatch")

    email = payload.get("email")
    if not email:
        raise OIDCError("id_token has no email claim")
    return OIDCIdentity(
        subject=str(payload.get("sub", "")),
        email=str(email),
        name=str(payload.get("name") or payload.get("preferred_username") or ""),
        email_verified=bool(payload.get("email_verified", False)),
    )
