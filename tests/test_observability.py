"""Observability: liveness/readiness probes, metrics format, alert evaluation."""

from __future__ import annotations

from fastapi.testclient import TestClient

from env.api import app

client = TestClient(app)


def test_version_endpoint():
    from env import __version__

    resp = client.get("/version")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "autonomous-executive-email-copilot"
    # Single-sourced from the package version (env/__init__.py / pyproject).
    assert body["version"] == __version__


def test_liveness_is_cheap_and_ok():
    resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json()["status"] == "alive"


def test_readiness_reports_ready_when_db_reachable():
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


def test_metrics_is_prometheus_text():
    # Make a request so counters are populated, then scrape.
    client.get("/health")
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "requests_total" in resp.text


def test_alerts_endpoint_evaluates_rules():
    resp = client.get("/alerts")
    assert resp.status_code == 200
    body = resp.json()
    # Rule evaluation is wired: the endpoint returns the active/known alert sets.
    assert "active_alerts" in body
    assert "all_alerts" in body
    assert isinstance(body["active_alerts"], list)
