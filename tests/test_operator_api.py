"""The operator API: the sales-led deployment's provisioning and licensing desk.

Auth posture first (absent when unconfigured, constant-time token when not),
then the full customer lifecycle: provision -> appears in the org list -> mint
-> customer activates -> revoke -> activation refused and entitlement dead.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app

OPERATOR_TOKEN = "test-operator-token"


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def operator(monkeypatch):
    monkeypatch.setenv("OPERATOR_TOKEN", OPERATOR_TOKEN)
    return {"Authorization": f"Bearer {OPERATOR_TOKEN}"}


def _cleanup_org(org_id: str) -> None:
    from app.saas.data_lifecycle import DataLifecycleService

    DataLifecycleService().delete_org(org_id)


class TestOperatorAuth:
    def test_surface_is_absent_when_unconfigured(self, client, monkeypatch):
        monkeypatch.delenv("OPERATOR_TOKEN", raising=False)
        assert client.get("/operator/orgs").status_code == 404
        assert client.get("/operator/leads").status_code == 404

    def test_wrong_token_is_401(self, client, operator):
        assert (
            client.get("/operator/orgs", headers={"Authorization": "Bearer nope"}).status_code
            == 401
        )
        # Reads require the token too — stricter than the benchmark gateway.
        assert client.get("/operator/orgs").status_code == 401

    def test_x_operator_token_header_works(self, client, operator):
        response = client.get("/operator/orgs", headers={"X-Operator-Token": OPERATOR_TOKEN})
        assert response.status_code == 200

    def test_operator_routes_bypass_the_api_auth_gateway(self, client, operator, monkeypatch):
        """API_AUTH_TOKEN must not double-gate /operator/*; the operator token
        alone decides."""
        monkeypatch.setenv("API_AUTH_TOKEN", "an-unrelated-operator-gateway-token")
        assert client.get("/operator/orgs", headers=operator).status_code == 200


class TestCustomerLifecycle:
    def test_provision_mint_activate_revoke(self, client, operator):
        marker = uuid.uuid4().hex[:10]
        owner_email = f"owner-{marker}@customer.example"

        # Provision (works even with self-serve signup off).
        provisioned = client.post(
            "/operator/orgs",
            headers=operator,
            json={
                "org_name": f"Acme {marker}",
                "owner_email": owner_email,
                "owner_name": "Pat Doe",
            },
        ).json()
        org_id = provisioned["organization"]["id"]
        temp_password = provisioned["temp_password"]
        assert temp_password
        assert provisioned["entitlement"]["plan"] == "trial"
        try:
            # The workspace shows up on the customer list with its org_id.
            listing = client.get("/operator/orgs", headers=operator).json()
            row = next(r for r in listing["organizations"] if r["organization"]["id"] == org_id)
            assert row["owner_email"] == owner_email
            assert row["entitlement"]["is_valid"]

            # The customer can sign in with the temp password.
            login = client.post(
                "/auth/login", json={"email": owner_email, "password": temp_password}
            )
            assert login.status_code == 200
            customer = {"Authorization": f"Bearer {login.json()['access_token']}"}

            # Mint a paid license; the customer activates it.
            minted = client.post(
                "/operator/licenses",
                headers=operator,
                json={"org_id": org_id, "plan": "business", "valid_days": 365},
            ).json()
            assert minted["terms"]["plan"] == "business"
            activated = client.post(
                "/billing/activate-license",
                headers=customer,
                json={"license_key": minted["license_key"]},
            )
            assert activated.status_code == 200
            entitlement = client.get("/billing/entitlement", headers=customer).json()
            assert entitlement["plan"] == "business"
            assert entitlement["is_valid"]

            # Revoking the named key is a downgrade: the entitlement falls back
            # to the next most recent active license (the original trial).
            revoked = client.post(
                "/operator/licenses/revoke",
                headers=operator,
                json={"org_id": org_id, "key_id": minted["terms"]["key_id"]},
            )
            assert revoked.status_code == 200
            reactivate = client.post(
                "/billing/activate-license",
                headers=customer,
                json={"license_key": minted["license_key"]},
            )
            assert reactivate.status_code in (400, 402, 403)
            entitlement = client.get("/billing/entitlement", headers=customer).json()
            assert entitlement["plan"] == "trial"

            # Revoking without a key is the full cut-off: entitlement dead.
            cutoff = client.post(
                "/operator/licenses/revoke", headers=operator, json={"org_id": org_id}
            )
            assert cutoff.status_code == 200
            assert cutoff.json()["revoked"] >= 1
            entitlement = client.get("/billing/entitlement", headers=customer).json()
            assert not entitlement["is_valid"]
        finally:
            _cleanup_org(org_id)

    def test_provision_rejects_a_taken_email(self, client, operator):
        marker = uuid.uuid4().hex[:10]
        email = f"dupe-{marker}@customer.example"
        first = client.post(
            "/operator/orgs",
            headers=operator,
            json={"org_name": f"First {marker}", "owner_email": email},
        )
        assert first.status_code == 200
        try:
            second = client.post(
                "/operator/orgs",
                headers=operator,
                json={"org_name": f"Second {marker}", "owner_email": email},
            )
            assert second.status_code == 409
        finally:
            _cleanup_org(first.json()["organization"]["id"])

    def test_mint_for_unknown_org_is_404(self, client, operator):
        response = client.post(
            "/operator/licenses",
            headers=operator,
            json={"org_id": "no-such-org", "plan": "business"},
        )
        assert response.status_code == 404

    def test_revoke_unknown_license_is_404(self, client, operator):
        response = client.post(
            "/operator/licenses/revoke",
            headers=operator,
            json={"org_id": "no-such-org", "key_id": "no-such-key"},
        )
        assert response.status_code == 404


class TestLeads:
    def test_leads_list_and_status_flow(self, client, operator):
        marker = uuid.uuid4().hex[:10]
        # Capture through the public endpoint, exactly as a prospect would.
        captured = client.post(
            "/billing/contact-sales",
            json={"email": f"prospect-{marker}@example.com", "company": "Prospect Co"},
        )
        assert captured.status_code == 200

        leads = client.get("/operator/leads", headers=operator).json()["leads"]
        lead = next(le for le in leads if le["email"] == f"prospect-{marker}@example.com")
        assert lead["status"] == "new"

        updated = client.patch(
            f"/operator/leads/{lead['id']}", headers=operator, json={"status": "contacted"}
        ).json()
        assert updated["status"] == "contacted"

    def test_bad_status_is_rejected(self, client, operator):
        assert (
            client.patch(
                "/operator/leads/1", headers=operator, json={"status": "not-a-status"}
            ).status_code
            == 422
        )
