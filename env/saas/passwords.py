"""Password hashing using PBKDF2-HMAC-SHA256 from the standard library.

We deliberately avoid a compiled dependency (bcrypt/argon2) so the SaaS layer
adds zero new pinned wheels and builds identically across the CI matrix. PBKDF2
with a high iteration count and a per-password random salt is a FIPS-approved
KDF and is appropriate for storing account credentials.

Stored format (single opaque string, self-describing so the iteration count can
be raised over time without breaking old hashes)::

    pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

ALGORITHM = "pbkdf2_sha256"
DEFAULT_ITERATIONS = 210_000
_SALT_BYTES = 16
_HASH_BYTES = 32


def _b64encode(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64decode(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def hash_password(password: str, *, iterations: int = DEFAULT_ITERATIONS) -> str:
    """Return a self-describing PBKDF2 hash for ``password``.

    A fresh random salt is generated per call, so hashing the same password
    twice yields different strings — as it must.
    """
    if not password:
        raise ValueError("password must not be empty")
    if iterations < 1:
        raise ValueError("iterations must be positive")
    salt = os.urandom(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, _HASH_BYTES)
    return f"{ALGORITHM}${iterations}${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """Verify ``password`` against a stored PBKDF2 hash in constant time.

    Returns False (never raises) for malformed stored values, so a corrupt row
    can't crash a login handler.
    """
    if not password or not stored:
        return False
    try:
        algorithm, iter_str, salt_b64, hash_b64 = stored.split("$")
        if algorithm != ALGORITHM:
            return False
        iterations = int(iter_str)
        salt = _b64decode(salt_b64)
        expected = _b64decode(hash_b64)
    except (ValueError, base64.binascii.Error):  # type: ignore[attr-defined]
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations, len(expected)
    )
    return hmac.compare_digest(candidate, expected)


def needs_rehash(stored: str, *, iterations: int = DEFAULT_ITERATIONS) -> bool:
    """True if ``stored`` uses an older/weaker parameter set and should be
    re-hashed on the next successful login (transparent upgrade path)."""
    try:
        algorithm, iter_str, _salt, _hash = stored.split("$")
    except ValueError:
        return True
    return algorithm != ALGORITHM or int(iter_str) < iterations
