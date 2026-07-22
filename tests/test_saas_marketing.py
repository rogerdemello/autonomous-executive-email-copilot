"""Tests for the marketing surface (landing + pricing) and pricing/plan sync."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from env.api import app
from env.saas import licensing


@pytest.fixture
def client():
    return TestClient(app)


def test_landing_renders(client):
    resp = client.get("/welcome")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Executive Email Copilot" in resp.text
    assert "Start free trial" in resp.text


def test_security_txt_served(client):
    resp = client.get("/.well-known/security.txt")
    assert resp.status_code == 200
    assert "Contact:" in resp.text
    assert "Expires:" in resp.text


def test_pricing_page_lists_all_plans(client):
    resp = client.get("/pricing")
    assert resp.status_code == 200
    for plan in licensing.PLANS.values():
        assert plan.name in resp.text
        assert plan.price_display in resp.text


def test_pricing_json_matches_registry(client):
    resp = client.get("/api/pricing")
    assert resp.status_code == 200
    plans = {p["key"]: p for p in resp.json()["plans"]}
    assert set(plans) == set(licensing.PLANS)
    for key, plan in licensing.PLANS.items():
        assert plans[key]["seats"] == plan.seats
        assert plans[key]["features"] == list(plan.features)


def test_pricing_is_public_even_with_operator_token(client, monkeypatch):
    # Marketing pages are GET (non-mutating) so they stay reachable regardless of
    # the operator API_AUTH_TOKEN gate.
    monkeypatch.setenv("API_AUTH_TOKEN", "operator-secret")
    assert client.get("/pricing").status_code == 200
    assert client.get("/welcome").status_code == 200
