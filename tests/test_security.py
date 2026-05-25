"""Tests for opt-in API security: auth, CORS, rate limiting, input hardening."""

from __future__ import annotations

from fastapi.testclient import TestClient

from env.api import app
from env.security import rate_limiter

client = TestClient(app)


# --- Auth (opt-in via API_AUTH_TOKEN) ---


def test_open_when_no_token_configured(monkeypatch):
    monkeypatch.delenv("API_AUTH_TOKEN", raising=False)
    resp = client.post("/reset", json={"task_id": "easy_classification", "seed": 1})
    assert resp.status_code == 200


def test_mutating_requires_token_when_configured(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "s3cret")
    # No credentials -> rejected.
    assert client.post("/reset", json={}).status_code == 401
    # Wrong token -> rejected.
    bad = client.post("/reset", json={}, headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401
    # Correct bearer token -> allowed.
    ok = client.post("/reset", json={}, headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200
    # X-API-Key header also works.
    ok2 = client.post("/reset", json={}, headers={"X-API-Key": "s3cret"})
    assert ok2.status_code == 200


def test_reads_open_even_when_token_configured(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "s3cret")
    assert client.get("/health").status_code == 200
    assert client.get("/tasks").status_code == 200


# --- CORS ---


def test_cors_header_present():
    resp = client.get("/health", headers={"Origin": "http://example.com"})
    assert resp.status_code == 200
    assert "access-control-allow-origin" in {k.lower() for k in resp.headers}


# --- Rate limiting (opt-in via RATE_LIMIT_PER_MINUTE) ---


def test_rate_limit_trips_when_enabled(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1")
    rate_limiter.reset()
    first = client.get("/health")
    second = client.get("/health")
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.headers.get("Retry-After") == "60"
    rate_limiter.reset()


def test_no_rate_limit_by_default(monkeypatch):
    monkeypatch.delenv("RATE_LIMIT_PER_MINUTE", raising=False)
    rate_limiter.reset()
    for _ in range(5):
        assert client.get("/health").status_code == 200


# --- Input hardening ---


def test_invalid_episode_id_rejected():
    assert client.get("/episodes/bad!id").status_code == 400
    assert client.get("/replay/bad!id").status_code == 400


def test_pagination_is_clamped():
    payload = client.get("/episodes", params={"page": -5, "limit": 9999}).json()
    assert payload["page"] == 1
    assert payload["limit"] == 100
