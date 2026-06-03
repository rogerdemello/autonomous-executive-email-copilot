"""Tests for opt-in multi-tenant API auth (API_TENANTS).

Multiple tenant tokens authenticate and tag an ``X-Tenant`` response header, the
single ``API_AUTH_TOKEN`` keeps working (resolving to the ``default`` tenant),
and the API stays open with no config set. Full per-tenant DB row isolation is
out of scope (no tenant column); these tests only cover auth + tagging.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from env.api import app
from env.config import get_settings
from env.security import rate_limiter, resolve_auth

client = TestClient(app)


# --- Config parsing ---


def test_tenant_token_map_parsing(monkeypatch):
    monkeypatch.setenv("API_TENANTS", "tokA:alpha, tokB:beta ,bad,empty:")
    mapping = get_settings().tenant_token_map
    assert mapping == {"tokA": "alpha", "tokB": "beta"}


def test_tenant_token_map_empty_by_default(monkeypatch):
    monkeypatch.delenv("API_TENANTS", raising=False)
    assert get_settings().tenant_token_map == {}


# --- Multi-tenant auth + X-Tenant tagging ---


def test_multiple_tenant_tokens_authenticate_and_tag(monkeypatch):
    monkeypatch.delenv("API_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("API_TENANTS", "tokA:alpha,tokB:beta")
    rate_limiter.reset()

    # No credentials -> rejected.
    assert client.post("/reset", json={}).status_code == 401

    # Tenant A token -> allowed and tagged.
    resp_a = client.post("/reset", json={}, headers={"Authorization": "Bearer tokA"})
    assert resp_a.status_code == 200
    assert resp_a.headers.get("X-Tenant") == "alpha"

    # Tenant B token via X-API-Key -> allowed and tagged.
    resp_b = client.post("/reset", json={}, headers={"X-API-Key": "tokB"})
    assert resp_b.status_code == 200
    assert resp_b.headers.get("X-Tenant") == "beta"

    # Unknown token -> rejected.
    assert client.post("/reset", json={}, headers={"X-API-Key": "nope"}).status_code == 401


def test_single_token_still_works_and_tags_default(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "s3cret")
    monkeypatch.delenv("API_TENANTS", raising=False)
    rate_limiter.reset()

    assert client.post("/reset", json={}).status_code == 401
    ok = client.post("/reset", json={}, headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200
    assert ok.headers.get("X-Tenant") == "default"


def test_single_and_tenant_tokens_coexist(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "s3cret")
    monkeypatch.setenv("API_TENANTS", "tokA:alpha")
    rate_limiter.reset()

    single = client.post("/reset", json={}, headers={"Authorization": "Bearer s3cret"})
    assert single.status_code == 200
    assert single.headers.get("X-Tenant") == "default"

    tenant = client.post("/reset", json={}, headers={"Authorization": "Bearer tokA"})
    assert tenant.status_code == 200
    assert tenant.headers.get("X-Tenant") == "alpha"


def test_open_and_untagged_when_no_config(monkeypatch):
    monkeypatch.delenv("API_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("API_TENANTS", raising=False)
    rate_limiter.reset()

    resp = client.post("/reset", json={"task_id": "easy_classification", "seed": 1})
    assert resp.status_code == 200
    # No tenant resolved -> no X-Tenant header (byte-identical default behavior).
    assert "X-Tenant" not in resp.headers


def test_reads_open_and_untagged_even_with_tenants(monkeypatch):
    # Non-mutating methods stay open and carry no tenant tag.
    monkeypatch.setenv("API_TENANTS", "tokA:alpha")
    rate_limiter.reset()
    resp = client.get("/health", headers={"Authorization": "Bearer tokA"})
    assert resp.status_code == 200
    assert "X-Tenant" not in resp.headers


# --- Per-tenant rate-limit keying ---


def test_rate_limit_keys_per_tenant(monkeypatch):
    monkeypatch.setenv("API_TENANTS", "tokA:alpha,tokB:beta")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1")
    rate_limiter.reset()

    # Tenant A's single allowed request, then A is throttled.
    assert client.post("/reset", json={}, headers={"X-API-Key": "tokA"}).status_code == 200
    assert client.post("/reset", json={}, headers={"X-API-Key": "tokA"}).status_code == 429
    # Tenant B has an independent window and is still allowed.
    assert client.post("/reset", json={}, headers={"X-API-Key": "tokB"}).status_code == 200
    rate_limiter.reset()


# --- resolve_auth unit coverage ---


def test_resolve_auth_open_when_unconfigured():
    assert resolve_auth("POST", None, None, None, None) == (True, None)
    assert resolve_auth("POST", None, {}, None, None) == (True, None)


def test_resolve_auth_non_mutating_is_open():
    authorized, tenant = resolve_auth("GET", None, {"tokA": "alpha"}, "Bearer tokA", None)
    assert authorized is True
    assert tenant is None


def test_resolve_auth_single_token_resolves_default():
    assert resolve_auth("POST", "s3cret", {}, "Bearer s3cret", None) == (True, "default")


def test_resolve_auth_tenant_token_resolves_label():
    assert resolve_auth("POST", None, {"tokA": "alpha"}, None, "tokA") == (True, "alpha")


def test_resolve_auth_rejects_unknown_token():
    assert resolve_auth("POST", "s3cret", {"tokA": "alpha"}, "Bearer nope", None) == (False, None)
    assert resolve_auth("POST", None, {"tokA": "alpha"}, None, None) == (False, None)
