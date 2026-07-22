"""Unit tests for the SaaS auth primitives: password hashing and session tokens."""

from __future__ import annotations

import time

import pytest

from env.saas import passwords, tokens


class TestPasswords:
    def test_hash_then_verify_roundtrip(self):
        stored = passwords.hash_password("correct horse battery staple")
        assert passwords.verify_password("correct horse battery staple", stored)

    def test_wrong_password_rejected(self):
        stored = passwords.hash_password("s3cret")
        assert not passwords.verify_password("s3cr3t", stored)

    def test_salt_makes_each_hash_unique(self):
        a = passwords.hash_password("same")
        b = passwords.hash_password("same")
        assert a != b
        assert passwords.verify_password("same", a)
        assert passwords.verify_password("same", b)

    def test_stored_format_is_self_describing(self):
        stored = passwords.hash_password("x", iterations=1000)
        algo, iters, salt, digest = stored.split("$")
        assert algo == "pbkdf2_sha256"
        assert iters == "1000"
        assert salt and digest

    def test_empty_password_rejected_on_hash(self):
        with pytest.raises(ValueError):
            passwords.hash_password("")

    @pytest.mark.parametrize("bad", ["", "not-a-hash", "pbkdf2_sha256$abc$def", "a$b$c$d"])
    def test_malformed_stored_never_raises(self, bad):
        assert passwords.verify_password("anything", bad) is False

    def test_needs_rehash_on_lower_iterations(self):
        stored = passwords.hash_password("x", iterations=1000)
        assert passwords.needs_rehash(stored, iterations=210_000)
        assert not passwords.needs_rehash(stored, iterations=1000)


class TestTokens:
    SECRET = "unit-test-secret"

    def test_encode_decode_roundtrip(self):
        token = tokens.encode(
            {"sub": "u1", "org": "o1", "role": "owner"}, self.SECRET, ttl_seconds=60
        )
        claims = tokens.decode(token, self.SECRET)
        assert claims["sub"] == "u1"
        assert claims["org"] == "o1"
        assert claims["role"] == "owner"
        assert claims["exp"] > claims["iat"]

    def test_tampered_payload_rejected(self):
        token = tokens.encode({"sub": "u1"}, self.SECRET, ttl_seconds=60)
        header, _payload, sig = token.split(".")
        forged = tokens.encode({"sub": "attacker"}, self.SECRET, ttl_seconds=60)
        forged_payload = forged.split(".")[1]
        tampered = f"{header}.{forged_payload}.{sig}"
        with pytest.raises(tokens.TokenError):
            tokens.decode(tampered, self.SECRET)

    def test_wrong_secret_rejected(self):
        token = tokens.encode({"sub": "u1"}, self.SECRET, ttl_seconds=60)
        with pytest.raises(tokens.TokenError):
            tokens.decode(token, "different-secret")

    def test_expired_token_rejected(self):
        past = time.time() - 10_000
        token = tokens.encode({"sub": "u1"}, self.SECRET, ttl_seconds=1, now=past)
        with pytest.raises(tokens.TokenError):
            tokens.decode(token, self.SECRET)

    def test_not_yet_expired_token_accepted_with_now(self):
        base = 1_000_000.0
        token = tokens.encode({"sub": "u1"}, self.SECRET, ttl_seconds=100, now=base)
        claims = tokens.decode(token, self.SECRET, now=base + 50)
        assert claims["sub"] == "u1"

    @pytest.mark.parametrize("bad", ["", "a.b", "a.b.c.d", "only-one-segment"])
    def test_malformed_token_raises(self, bad):
        with pytest.raises(tokens.TokenError):
            tokens.decode(bad, self.SECRET)
