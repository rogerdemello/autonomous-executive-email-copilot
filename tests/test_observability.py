"""Observability: liveness/readiness probes, metrics format, alert evaluation."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_version_endpoint():
    from app import __version__

    resp = client.get("/version")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "autonomous-executive-email-copilot"
    # Single-sourced from the package version (app/__init__.py / pyproject).
    assert body["version"] == __version__


def test_liveness_is_cheap_and_ok():
    resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json()["status"] == "alive"


def test_readiness_reports_ready_when_db_reachable():
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


def test_readiness_reports_not_ready_when_schema_is_behind(monkeypatch):
    """An unmigrated schema must fail the probe, not 200 into runtime errors.

    This is the case the migration move exists to serve: migrate_db() used to
    run at import, so a database that was unreachable or behind killed the
    process before FastAPI existed and this endpoint could never answer. Now
    the service comes up and reports why.
    """
    monkeypatch.setattr("app.main.schema_is_current", lambda: False)
    resp = client.get("/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["reason"] == "schema"


def test_app_imports_without_touching_the_database():
    """Importing the app must not require a reachable database.

    Regression guard: the schema migration is a startup concern (the lifespan),
    not an import-time side effect. When it ran at import, an unreachable
    database killed the process before FastAPI existed — a crash loop with no
    endpoint to diagnose it — instead of a 503 on /health/ready.

    Runs in a subprocess with DATABASE_URL pointed at a closed port. Importing
    in-process would either pollute other modules (app.main is imported
    everywhere) or prove nothing, since the engine is already built.
    """
    import subprocess
    import sys

    # A SQLite path whose parent is this file, not a directory. Opening it fails
    # on every platform (ENOTDIR), while create_engine still succeeds — SQLite's
    # driver is stdlib and connects lazily, so the only thing that can fail is
    # code that actually talks to the database at import time. A Postgres URL
    # would instead fail on the driver import, testing the wrong thing.
    unopenable = f"sqlite:///{os.path.abspath(__file__)}/nope.db"
    env = {**os.environ, "DATABASE_URL": unopenable}
    result = subprocess.run(
        [sys.executable, "-c", "import app.main; print('IMPORTED', app.main.app.title)"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert result.returncode == 0, f"import failed:\n{result.stderr}"
    assert "IMPORTED" in result.stdout


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


# A representative scrape: an unlabelled counter, an unlabelled gauge, a
# labelled counter spread over several label sets, and the comment lines a real
# exposition carries. Parsing is tested against this fixed text rather than the
# live registry, which is process-global and therefore carries whatever every
# previously-run test happened to record.
_SCRAPE = """\
# HELP requests_total Total requests
# TYPE requests_total counter
requests_total{method="GET",path="/health",status="200"} 6
requests_total{method="GET",path="/inbox",status="200"} 3
requests_total{method="POST",path="/inbox",status="500"} 1
# TYPE api_errors_total counter
api_errors_total{type="timeout"} 2
# TYPE episodes_completed_total counter
episodes_completed_total 8
episodes_failed_total 4
active_episodes 0
"""


def test_unlabelled_series_are_keyed_by_name_not_name_plus_value():
    """`episodes_failed_total 4` must key as `episodes_failed_total`, not the pair.

    Taking the name as everything before the first `{` swept the value into the
    key for every unlabelled metric, so high_failure_rate_rule — which reads
    episodes_completed_total and episodes_failed_total — could never see them.
    """
    from app.main import parse_metrics_text

    parsed = parse_metrics_text(_SCRAPE)

    assert parsed["episodes_completed_total"] == 8
    assert parsed["episodes_failed_total"] == 4
    assert parsed["active_episodes"] == 0
    assert not any(" " in key for key in parsed), f"value leaked into a key: {sorted(parsed)}"


def test_labelled_series_aggregate_under_their_bare_metric_name():
    """The alert rules read totals, so the parser must produce totals.

    ``record_request`` always passes labels, so every request series is keyed
    ``requests_total_method=..._path=..._status=...``. Without the aggregate,
    ``metrics["requests_total"]`` was always absent, which made
    high_error_rate_rule structurally incapable of firing however many errors
    the service returned.
    """
    from app.main import parse_metrics_text

    parsed = parse_metrics_text(_SCRAPE)

    # The aggregate is the sum of the label dimensions, not one of them.
    assert parsed["requests_total"] == pytest.approx(10)
    assert parsed["api_errors_total"] == pytest.approx(2)
    # Per-series identity is preserved alongside the total.
    assert parsed["requests_total_method=POST_path=/inbox_status=500"] == 1


def test_default_alert_rules_can_actually_fire():
    """Every default rule must be reachable from a realistic scrape.

    Each of these was previously unfirable: two because of the key bugs above,
    and cost_spike because it read a metric name nothing ever emitted.
    """
    from app.main import parse_metrics_text
    from telemetry.alerts import cost_spike_rule, high_error_rate_rule, high_failure_rate_rule

    parsed = parse_metrics_text(_SCRAPE + "llm_cost_usd_total 150.0\n")

    # 2 errors / 10 requests = 0.2 > 0.1
    assert high_error_rate_rule(threshold=0.1).condition(parsed) is True
    # 4 failed / 12 total = 0.33 > 0.2
    assert high_failure_rate_rule(threshold=0.2).condition(parsed) is True
    assert cost_spike_rule(threshold=100.0).condition(parsed) is True

    # And they stay quiet when they should.
    quiet = parse_metrics_text(_SCRAPE)
    assert high_error_rate_rule(threshold=0.5).condition(quiet) is False
    assert high_failure_rate_rule(threshold=0.9).condition(quiet) is False
    assert cost_spike_rule(threshold=100.0).condition(quiet) is False
