"""At-rest encryption for sensitive secrets (OAuth tokens).

OAuth access/refresh tokens are effectively long-lived credentials to a
customer's mailbox, so they must never be stored in plaintext. This module
provides a small authenticated cipher (encrypt-then-MAC) built on the standard
library, keyed from ``AUTH_SECRET_KEY``:

- Key derivation: PBKDF2-HMAC-SHA256(secret, fixed salt) -> 32-byte key.
- Confidentiality: a SHA-256 counter-mode keystream XOR the plaintext.
- Integrity/authenticity: HMAC-SHA256 over (version || nonce || ciphertext),
  verified in constant time before decryption is trusted.

The output is a versioned, base64 string so the scheme can evolve. If the
optional ``cryptography`` package is installed we prefer its audited Fernet
implementation automatically; otherwise this stdlib construction is used.

Rotating ``AUTH_SECRET_KEY`` makes existing ciphertexts undecryptable (they must
be re-linked) — same trade-off as session tokens.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

_KDF_SALT = b"mailbox-token-vault-v1"
_KDF_ITERATIONS = 100_000
_KEY_LEN = 32
_NONCE_LEN = 16
_TAG_LEN = 32
_VERSION = b"v1"
# Fernet-backed ciphertexts carry this prefix so both schemes can coexist and
# old ``v1$`` blobs stay decryptable after an upgrade.
_FERNET_PREFIX = "f1$"

try:  # Prefer the audited AEAD (AES-128-CBC + HMAC-SHA256) when available.
    from cryptography.fernet import Fernet, InvalidToken

    _FERNET_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    _FERNET_AVAILABLE = False


def _fernet_key(secret: str) -> bytes:
    """Derive a urlsafe-base64 32-byte Fernet key from the app secret."""
    raw = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), _KDF_SALT, _KDF_ITERATIONS, 32)
    return base64.urlsafe_b64encode(raw)


class DecryptionError(Exception):
    """Raised when a ciphertext is malformed or fails authentication."""


def _derive_key(secret: str) -> bytes:
    if not secret:
        raise ValueError("a non-empty secret is required")
    return hashlib.pbkdf2_hmac(
        "sha256", secret.encode("utf-8"), _KDF_SALT, _KDF_ITERATIONS, _KEY_LEN
    )


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    """SHA-256 counter-mode keystream of ``length`` bytes."""
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hashlib.sha256(key + nonce + counter.to_bytes(8, "big")).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def _xor(data: bytes, stream: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(data, stream, strict=True))


class TokenVault:
    """Encrypts/decrypts short secret strings with a key derived from ``secret``."""

    def __init__(self, secret: str) -> None:
        if not secret:
            raise ValueError("a non-empty secret is required")
        self._secret = secret
        self._key = _derive_key(secret)
        self._fernet = Fernet(_fernet_key(secret)) if _FERNET_AVAILABLE else None

    def encrypt(self, plaintext: str) -> str:
        if plaintext is None:
            raise ValueError("plaintext is required")
        # Prefer the audited Fernet AEAD; fall back to the stdlib construction.
        if self._fernet is not None:
            token = self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")
            return _FERNET_PREFIX + token
        data = plaintext.encode("utf-8")
        nonce = os.urandom(_NONCE_LEN)
        ciphertext = _xor(data, _keystream(self._key, nonce, len(data)))
        tag = hmac.new(self._key, _VERSION + nonce + ciphertext, hashlib.sha256).digest()
        blob = _VERSION + b"$" + base64.b64encode(nonce + tag + ciphertext)
        return blob.decode("ascii")

    def decrypt(self, token: str) -> str:
        if not token:
            raise DecryptionError("empty ciphertext")
        # Fernet-encrypted blobs (post-upgrade). Older v1$ blobs still decrypt
        # via the stdlib path below, so an upgrade needs no re-encryption.
        if token.startswith(_FERNET_PREFIX):
            if self._fernet is None:
                raise DecryptionError("Fernet ciphertext but cryptography is not installed")
            try:
                return self._fernet.decrypt(token[len(_FERNET_PREFIX) :].encode("ascii")).decode(
                    "utf-8"
                )
            except InvalidToken as exc:
                raise DecryptionError("authentication failed (tampered or wrong key)") from exc
        try:
            version, _, b64 = token.partition("$")
            if version.encode("ascii") != _VERSION or not b64:
                raise DecryptionError("unsupported ciphertext version")
            raw = base64.b64decode(b64.encode("ascii"))
        except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
            raise DecryptionError("malformed ciphertext") from exc
        if len(raw) < _NONCE_LEN + _TAG_LEN:
            raise DecryptionError("truncated ciphertext")
        nonce = raw[:_NONCE_LEN]
        tag = raw[_NONCE_LEN : _NONCE_LEN + _TAG_LEN]
        ciphertext = raw[_NONCE_LEN + _TAG_LEN :]
        expected = hmac.new(self._key, _VERSION + nonce + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, tag):
            raise DecryptionError("authentication failed (tampered or wrong key)")
        return _xor(ciphertext, _keystream(self._key, nonce, len(ciphertext))).decode("utf-8")


def get_vault() -> TokenVault:
    """Build a vault keyed from the app's configured signing secret."""
    from ..config import get_settings

    return TokenVault(get_settings().resolved_auth_secret)
