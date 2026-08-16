"""Signed session tokens (compact JWS, HS256) implemented with the stdlib.

We emit standard JWT-structured tokens (``header.payload.signature``,
base64url, ``alg=HS256``) so any client library can read them, but we sign and
verify with :mod:`hmac`/:mod:`hashlib` to avoid pulling PyJWT as a dependency.

A token is a bearer credential proving "this request is user U in org O with
role R, until time E". It is stateless: verification needs only the shared
secret, no database round-trip. Revocation of an individual token before expiry
is intentionally out of scope for the foundation (short TTLs mitigate it); org-
and license-level revocation is enforced separately at the data layer.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

ALGORITHM = "HS256"


class TokenError(Exception):
    """Raised when a token is malformed, mis-signed, or expired."""


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def _sign(signing_input: bytes, secret: str) -> str:
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return _b64url_encode(signature)


def encode(
    claims: dict[str, Any],
    secret: str,
    *,
    ttl_seconds: int,
    now: float | None = None,
) -> str:
    """Build a signed token carrying ``claims`` plus ``iat``/``exp`` timestamps.

    ``claims`` typically holds ``sub`` (user id), ``org`` (org id), and
    ``role``. ``ttl_seconds`` sets how long the token stays valid.
    """
    if not secret:
        raise TokenError("a non-empty signing secret is required")
    issued_at = int(time.time() if now is None else now)
    payload = dict(claims)
    payload["iat"] = issued_at
    payload["exp"] = issued_at + int(ttl_seconds)
    header = {"alg": ALGORITHM, "typ": "JWT"}
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = _sign(signing_input, secret)
    return f"{header_b64}.{payload_b64}.{signature}"


def decode(token: str, secret: str, *, now: float | None = None) -> dict[str, Any]:
    """Verify ``token``'s signature and expiry, returning its claims.

    Raises :class:`TokenError` on any tampering, wrong secret, or expiry. The
    signature is checked in constant time before the payload is trusted.
    """
    if not token or not secret:
        raise TokenError("token and secret are required")
    try:
        header_b64, payload_b64, signature = token.split(".")
    except ValueError as exc:
        raise TokenError("malformed token structure") from exc

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected_sig = _sign(signing_input, secret)
    if not hmac.compare_digest(expected_sig, signature):
        raise TokenError("signature verification failed")

    try:
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
    except (ValueError, json.JSONDecodeError) as exc:
        raise TokenError("token payload is not valid JSON") from exc

    if header.get("alg") != ALGORITHM:
        raise TokenError(f"unexpected signing algorithm: {header.get('alg')!r}")

    current = time.time() if now is None else now
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)) or current >= exp:
        raise TokenError("token has expired")

    return payload
