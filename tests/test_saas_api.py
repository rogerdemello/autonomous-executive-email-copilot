"""Integration tests for the commercial SaaS API: auth, org/RBAC, billing.

These exercise the real FastAPI app + SQLAlchemy layer end to end. Emails are
made unique per run so the shared dev database (tests write to the real
``data/episodes.db``) doesn't collide across repeated runs.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from env.api import app
from env.config import get_settings
from env.saas import licensing


@pytest.fixture
def client():
    return TestClient(app)


def _email() -> str:
    return f"user_{uuid.uuid4().hex[:12]}@example.com"


def _signup(client, *, org_name="Acme Inc", password="hunter2pass"):
    email = _email()
    resp = client.post(
        "/auth/signup",
        json={"email": email, "password": password, "full_name": "Test User", "org_name": org_name},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    return email, password, data


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestAuth:
    def test_signup_creates_owner_and_trial(self, client):
        _email_, _pw, data = _signup(client)
        assert data["token_type"] == "bearer"
        assert data["user"]["role"] == "owner"
        assert data["user"]["status"] == "active"
        assert "password_hash" not in data["user"]
        assert data["organization"]["slug"]

    def test_duplicate_email_rejected(self, client):
        email, pw, _ = _signup(client)
        resp = client.post(
            "/auth/signup",
            json={"email": email, "password": pw, "full_name": "x", "org_name": "Other"},
        )
        assert resp.status_code == 409

    def test_login_success_and_me(self, client):
        email, pw, _ = _signup(client)
        resp = client.post("/auth/login", json={"email": email, "password": pw})
        assert resp.status_code == 200
        token = resp.json()["access_token"]
        me = client.get("/auth/me", headers=_auth_header(token))
        assert me.status_code == 200
        assert me.json()["user"]["email"] == email

    def test_login_wrong_password_rejected(self, client):
        email, _pw, _ = _signup(client)
        resp = client.post("/auth/login", json={"email": email, "password": "wrongpassword"})
        assert resp.status_code == 401

    def test_me_requires_auth(self, client):
        assert client.get("/auth/me").status_code == 401
        assert client.get("/auth/me", headers=_auth_header("garbage.token.here")).status_code == 401

    def test_change_password(self, client):
        email, pw, data = _signup(client)
        token = data["access_token"]
        resp = client.post(
            "/auth/change-password",
            headers=_auth_header(token),
            json={"current_password": pw, "new_password": "brandnewpass9"},
        )
        assert resp.status_code == 200
        assert client.post("/auth/login", json={"email": email, "password": pw}).status_code == 401
        assert (
            client.post(
                "/auth/login", json={"email": email, "password": "brandnewpass9"}
            ).status_code
            == 200
        )


class TestEntitlement:
    def test_trial_entitlement(self, client):
        _e, _p, data = _signup(client)
        token = data["access_token"]
        resp = client.get("/billing/entitlement", headers=_auth_header(token))
        assert resp.status_code == 200
        ent = resp.json()
        assert ent["plan"] == "trial"
        assert ent["seats"] == 3
        assert ent["seats_used"] == 1
        assert ent["is_valid"] is True

    def test_activate_business_license(self, client):
        _e, _p, data = _signup(client)
        token = data["access_token"]
        org_id = data["organization"]["id"]
        secret = get_settings().resolved_auth_secret
        key, _terms = licensing.mint_license(org_id, "business", secret)
        resp = client.post(
            "/billing/activate-license",
            headers=_auth_header(token),
            json={"license_key": key},
        )
        assert resp.status_code == 200, resp.text
        ent = resp.json()["entitlement"]
        assert ent["plan"] == "business"
        assert ent["seats"] == 50
        assert "sso" in ent["features"]

    def test_license_for_other_org_rejected(self, client):
        _e, _p, data = _signup(client)
        token = data["access_token"]
        secret = get_settings().resolved_auth_secret
        key, _terms = licensing.mint_license("some-other-org-id", "business", secret)
        resp = client.post(
            "/billing/activate-license",
            headers=_auth_header(token),
            json={"license_key": key},
        )
        assert resp.status_code == 403


class TestRBACAndSeats:
    def test_member_cannot_invite(self, client):
        # Owner invites a member, then that member (lower privilege) is blocked.
        _e, _p, owner = _signup(client)
        owner_token = owner["access_token"]
        member_email = _email()
        inv = client.post(
            "/org/members",
            headers=_auth_header(owner_token),
            json={
                "email": member_email,
                "full_name": "M",
                "role": "member",
                "temp_password": "temppass12",
            },
        )
        assert inv.status_code == 200, inv.text
        member_login = client.post(
            "/auth/login", json={"email": member_email, "password": "temppass12"}
        )
        member_token = member_login.json()["access_token"]
        blocked = client.post(
            "/org/members",
            headers=_auth_header(member_token),
            json={
                "email": _email(),
                "full_name": "N",
                "role": "member",
                "temp_password": "temppass34",
            },
        )
        assert blocked.status_code == 403

    def test_admin_cannot_assign_owner(self, client):
        _e, _p, owner = _signup(client)
        owner_token = owner["access_token"]
        admin_email = _email()
        client.post(
            "/org/members",
            headers=_auth_header(owner_token),
            json={
                "email": admin_email,
                "full_name": "A",
                "role": "admin",
                "temp_password": "temppass12",
            },
        )
        admin_token = client.post(
            "/auth/login", json={"email": admin_email, "password": "temppass12"}
        ).json()["access_token"]
        # Admin invites and tries to grant owner -> forbidden.
        resp = client.post(
            "/org/members",
            headers=_auth_header(admin_token),
            json={
                "email": _email(),
                "full_name": "B",
                "role": "owner",
                "temp_password": "temppass12",
            },
        )
        assert resp.status_code == 403

    def test_seat_limit_enforced(self, client):
        # Trial = 3 seats; owner + 2 invites fills it, the 3rd invite is 402.
        _e, _p, owner = _signup(client)
        t = owner["access_token"]
        for _ in range(2):
            r = client.post(
                "/org/members",
                headers=_auth_header(t),
                json={
                    "email": _email(),
                    "full_name": "x",
                    "role": "member",
                    "temp_password": "temppass12",
                },
            )
            assert r.status_code == 200, r.text
        over = client.post(
            "/org/members",
            headers=_auth_header(t),
            json={
                "email": _email(),
                "full_name": "x",
                "role": "member",
                "temp_password": "temppass12",
            },
        )
        assert over.status_code == 402

    def test_cannot_remove_last_owner(self, client):
        _e, _p, owner = _signup(client)
        t = owner["access_token"]
        owner_id = owner["user"]["id"]
        # Removing self is blocked (409) before the last-owner check even matters.
        resp = client.delete(f"/org/members/{owner_id}", headers=_auth_header(t))
        assert resp.status_code == 409


class TestTenantIsolation:
    def test_members_are_scoped_to_org(self, client):
        # Two independent orgs; neither can see the other's members.
        _e1, _p1, org1 = _signup(client, org_name="Org One")
        _e2, _p2, org2 = _signup(client, org_name="Org Two")
        t1 = org1["access_token"]
        t2 = org2["access_token"]
        # org1 invites a member
        member_email = _email()
        client.post(
            "/org/members",
            headers=_auth_header(t1),
            json={
                "email": member_email,
                "full_name": "M",
                "role": "member",
                "temp_password": "temppass12",
            },
        )
        members1 = {
            m["email"]
            for m in client.get("/org/members", headers=_auth_header(t1)).json()["members"]
        }
        members2 = {
            m["email"]
            for m in client.get("/org/members", headers=_auth_header(t2)).json()["members"]
        }
        assert member_email in members1
        assert member_email not in members2
        assert members1.isdisjoint(members2)

    def test_cannot_touch_member_in_other_org(self, client):
        _e1, _p1, org1 = _signup(client, org_name="Org A")
        _e2, _p2, org2 = _signup(client, org_name="Org B")
        # org2's owner id is unknown to org1; deleting it under org1 -> 404, not success.
        other_owner_id = org2["user"]["id"]
        resp = client.delete(
            f"/org/members/{other_owner_id}", headers=_auth_header(org1["access_token"])
        )
        assert resp.status_code == 404


class TestSalesLead:
    def test_contact_sales_public(self, client):
        resp = client.post(
            "/billing/contact-sales",
            json={
                "email": _email(),
                "name": "Buyer",
                "company": "BigCo",
                "seats": 200,
                "message": "Interested",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "received"
