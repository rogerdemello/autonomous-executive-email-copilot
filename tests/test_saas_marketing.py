"""Tests for the marketing surface (landing, security.txt, no public pricing)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


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


def test_pricing_page_redirects_home(client):
    # Sales-led product: there is no public pricing page; old links land home.
    resp = client.get("/pricing", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["location"] == "/"


def test_pricing_json_is_gone(client):
    assert client.get("/api/pricing").status_code == 404


def test_landing_has_no_pricing_links(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "/pricing" not in resp.text


def test_marketing_is_public_even_with_operator_token(client, monkeypatch):
    # Marketing pages are GET (non-mutating) so they stay reachable regardless of
    # the operator API_AUTH_TOKEN gate.
    monkeypatch.setenv("API_AUTH_TOKEN", "operator-secret")
    assert client.get("/welcome").status_code == 200
