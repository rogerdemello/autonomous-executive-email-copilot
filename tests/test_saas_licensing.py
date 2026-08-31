"""Unit tests for sales-led licensing: mint, verify, entitlement terms, RBAC."""

from __future__ import annotations

import time

import pytest

from app.saas import licensing, rbac


class TestLicensing:
    SECRET = "license-test-secret"

    def test_mint_and_verify_roundtrip(self):
        key, terms = licensing.mint_license("org1", "business", self.SECRET)
        verified = licensing.verify_license_key(key, self.SECRET)
        assert verified.org_id == "org1"
        assert verified.plan == "business"
        assert verified.seats == 50
        assert verified.key_id == terms.key_id
        assert "sso" in verified.features

    def test_seat_override(self):
        _key, terms = licensing.mint_license("org1", "team", self.SECRET, seats=25)
        assert terms.seats == 25

    def test_a_key_carries_its_features_and_seats(self):
        """Entitlement is a snapshot of the *key*, and nothing more.

        It used to grow `has_feature` and `seats_ok` helpers that BillingService
        independently re-implemented — and BillingService is the one the
        product calls, because a live entitlement is the signed terms AND the
        persisted row's status. This dataclass only knows the first half, so
        answering the question here would answer it wrong for a revoked key.
        """
        _key, terms = licensing.mint_license("org1", "enterprise", self.SECRET)
        assert "custom_models" in terms.features
        assert "nonexistent" not in terms.features
        assert terms.seats == licensing.PLANS["enterprise"].seats

    def test_wrong_secret_rejected(self):
        key, _terms = licensing.mint_license("org1", "team", self.SECRET)
        with pytest.raises(licensing.LicenseError):
            licensing.verify_license_key(key, "attacker-secret")

    def test_expired_license_rejected(self):
        past = time.time() - 10_000_000
        key, _terms = licensing.mint_license("org1", "trial", self.SECRET, valid_days=1, now=past)
        with pytest.raises(licensing.LicenseError):
            licensing.verify_license_key(key, self.SECRET)

    def test_unknown_plan_rejected(self):
        with pytest.raises(licensing.LicenseError):
            licensing.mint_license("org1", "platinum", self.SECRET)

    def test_session_token_is_not_a_license(self):
        from app.saas import tokens

        session = tokens.encode({"sub": "u1", "org": "o1"}, self.SECRET, ttl_seconds=60)
        with pytest.raises(licensing.LicenseError):
            licensing.verify_license_key(session, self.SECRET)

    def test_all_plans_are_mintable(self):
        for plan_key in licensing.PLANS:
            key, terms = licensing.mint_license("orgX", plan_key, self.SECRET)
            assert licensing.verify_license_key(key, self.SECRET).plan == plan_key


class TestRBAC:
    def test_role_ordering(self):
        assert rbac.role_at_least("owner", "admin")
        assert rbac.role_at_least("admin", "member")
        assert not rbac.role_at_least("member", "admin")

    def test_management_permissions(self):
        assert rbac.can_manage_members("admin")
        assert not rbac.can_manage_members("member")
        assert rbac.can_manage_billing("owner")
        assert not rbac.can_manage_billing("admin")

    def test_no_privilege_escalation(self):
        # An admin cannot mint an owner; an owner can assign anything.
        assert not rbac.can_assign_role("admin", "owner")
        assert rbac.can_assign_role("admin", "member")
        assert rbac.can_assign_role("owner", "owner")
        assert not rbac.can_assign_role("member", "member")
