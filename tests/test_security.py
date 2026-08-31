"""Tests for opt-in API security: auth, CORS, rate limiting, input hardening."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.security import rate_limiter
from app.main import app

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
    # Bare token without "Bearer" prefix -> rejected (must follow Bearer <token> format).
    bare = client.post("/reset", json={}, headers={"Authorization": "s3cret"})
    assert bare.status_code == 401
    # X-API-Key header also works.
    ok2 = client.post("/reset", json={}, headers={"X-API-Key": "s3cret"})
    assert ok2.status_code == 200


def test_reads_open_even_when_token_configured(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "s3cret")
    assert client.get("/health").status_code == 200
    assert client.get("/tasks").status_code == 200


def test_sensitive_reads_require_the_token_when_configured(monkeypatch):
    """Pending approvals, episode/preference stores, and the live sim state are
    not anonymous reads on a locked-down deployment — /approval/pending used to
    be a cross-tenant read with no credential at all."""
    monkeypatch.setenv("API_AUTH_TOKEN", "s3cret")
    for path in ("/approval/pending", "/episodes", "/preferences/users", "/dashboard/state"):
        assert client.get(path).status_code == 401, path
        assert client.get(path, headers={"Authorization": "Bearer s3cret"}).status_code == 200, path


def test_sensitive_reads_stay_open_without_a_token(monkeypatch):
    monkeypatch.delenv("API_AUTH_TOKEN", raising=False)
    assert client.get("/approval/pending").status_code == 200
    assert client.get("/episodes").status_code == 200


def test_metrics_require_the_token_when_configured(monkeypatch):
    """Prometheus output names paths, error classes, and episode volume —
    operational detail that shouldn't be an anonymous read on a locked-down
    deployment."""
    monkeypatch.setenv("API_AUTH_TOKEN", "s3cret")
    assert client.get("/metrics").status_code == 401
    ok = client.get("/metrics", headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200

    monkeypatch.delenv("API_AUTH_TOKEN", raising=False)
    assert client.get("/metrics").status_code == 200


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


# --- The simulator surface stays closed when a token is configured --------- #
class TestSimulatorSurfaceIsLocked:
    """The benchmark is a credibility asset, not part of the public product.

    With `API_AUTH_TOKEN` set (as `render.yaml` does), every simulator route
    must need it: the writes because they mutate, and the reads because they
    return episode data, learning examples, live sim state, and operational
    metrics. Each entry below is a path an anonymous visitor could once reach
    on a deployment that believed it was locked down.
    """

    WRITES = ("/reset", "/step", "/grader", "/baseline", "/leaderboard", "/benchmark/run")
    READS = (
        "/state",
        "/episodes",
        "/episodes/stats",
        "/replay/anything",
        "/approval/pending",
        "/approval/history",
        "/preferences/users",
        "/feedback",
        "/learning/stats",
        "/dashboard/state",
        "/metrics",
        "/alerts",
        # A rendered episode report is the same data as /episodes, in a PDF.
        # It was the one read here that stayed anonymous with a token set.
        "/reports/episode/anything",
    )

    @pytest.mark.parametrize("path", WRITES)
    def test_writes_require_the_token(self, monkeypatch, path):
        monkeypatch.setenv("API_AUTH_TOKEN", "s3cret")
        client = TestClient(app)
        assert client.post(path, json={}).status_code == 401, path

    @pytest.mark.parametrize("path", READS)
    def test_reads_require_the_token(self, monkeypatch, path):
        monkeypatch.setenv("API_AUTH_TOKEN", "s3cret")
        client = TestClient(app)
        assert client.get(path).status_code == 401, path

    @pytest.mark.parametrize("path", READS)
    def test_the_same_reads_stay_open_with_no_token(self, monkeypatch, path):
        """Open by default is the posture for local and evaluation use; the
        lock is something an operator turns on, not something that appears."""
        monkeypatch.delenv("API_AUTH_TOKEN", raising=False)
        client = TestClient(app)
        assert client.get(path).status_code != 401, path

    def test_the_product_is_not_locked_by_the_operator_token(self, monkeypatch):
        """The SaaS surface authenticates per user and must keep working when
        the benchmark surface is closed — otherwise turning the lock on takes
        the product down with it."""
        monkeypatch.setenv("API_AUTH_TOKEN", "s3cret")
        client = TestClient(app)
        assert client.get("/").status_code == 200
        assert client.get("/login").status_code == 200
        assert client.get("/privacy").status_code == 200
        assert client.get("/health").status_code == 200
