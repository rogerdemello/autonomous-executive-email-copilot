"""The background worker has to be able to say it is still alive.

Every pass used to be logged and dropped. A worker whose task had died was
therefore invisible: ``/health/ready`` still returned 200, and the only symptom
was an approval queue that stopped filling — which is exactly what a genuinely
quiet mailbox looks like. The product would have appeared to work while doing
none of the thing it is for.

The design decision under test is that a dead worker is *reported*, not fatal:
the probe keeps returning 200 because the web tier still serves pages,
approvals and sends, and a 503 on a single-instance deployment would take the
whole product down to fix a background job.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app, worker_status
from app.saas.sync_worker import BackgroundSyncWorker

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)


def _worker(**kwargs) -> BackgroundSyncWorker:
    kwargs.setdefault("poll_seconds", 30)
    kwargs.setdefault("interval_seconds", 900)
    return BackgroundSyncWorker(**kwargs)


class TestTheHeartbeat:
    def test_a_worker_that_has_never_run_reports_nothing(self):
        beat = _worker().heartbeat(now=NOW)

        assert beat["passes"] == 0
        assert beat["last_pass_at"] is None
        assert beat["seconds_since_last_pass"] is None
        assert beat["running"] is False
        # Nothing to be stale *from* yet — it was never started.
        assert beat["stale"] is False

    def test_a_pass_updates_the_heartbeat(self):
        worker = _worker()
        worker._record_pass({"checked": 3, "synced": 2, "errors": 0}, NOW)

        beat = worker.heartbeat(now=NOW)

        assert beat["passes"] == 1
        assert beat["last_pass_at"] == NOW.isoformat()
        assert beat["seconds_since_last_pass"] == 0
        assert beat["last_pass"]["synced"] == 2

    def test_staleness_is_measured_against_three_polls(self):
        worker = _worker(poll_seconds=30)
        worker._record_pass({"checked": 1}, NOW)

        assert worker.stale_after_seconds() == 90
        assert worker.heartbeat(now=NOW + timedelta(seconds=89))["stale"] is False
        assert worker.heartbeat(now=NOW + timedelta(seconds=91))["stale"] is True

    def test_a_worker_that_dies_before_its_first_pass_still_goes_stale(self):
        """The one case nobody is watching: it never worked at all."""
        worker = _worker(poll_seconds=30)
        worker.started_at = NOW

        assert worker.heartbeat(now=NOW + timedelta(seconds=10))["stale"] is False
        assert worker.heartbeat(now=NOW + timedelta(seconds=300))["stale"] is True

    def test_recent_passes_are_kept_but_bounded(self):
        worker = _worker()
        for i in range(25):
            worker._record_pass({"checked": i}, NOW + timedelta(seconds=i))

        beat = worker.heartbeat(now=NOW)

        assert beat["passes"] == 25
        assert len(beat["recent"]) == BackgroundSyncWorker._RECENT_LIMIT
        # The *last* ten, not the first ten.
        assert beat["recent"][-1]["checked"] == 24

    def test_a_real_sweep_records_a_pass(self):
        """The heartbeat is written by the sweep itself, not by a caller."""
        worker = _worker()
        worker.sync_due_connections(now=NOW)

        assert worker.passes == 1
        assert worker.last_pass_at == NOW
        assert "checked" in (worker.last_pass or {})


class TestACrashedPassStillBeats:
    def test_a_pass_that_throws_is_recorded_as_a_crash(self, monkeypatch):
        """Staleness must mean "nothing is sweeping", not "sweeps are failing".

        Conflating the two would hide whichever fault arrived second.
        """
        worker = _worker(poll_seconds=0.01)

        def _explode(*args, **kwargs):
            raise RuntimeError("the database went away")

        monkeypatch.setattr(worker, "sync_due_connections", _explode)

        async def _run_one_pass():
            task = worker.start()
            await asyncio.sleep(0.1)
            await worker.stop()
            return task

        asyncio.run(_run_one_pass())

        assert worker.passes >= 1
        assert worker.last_pass is not None
        assert worker.last_pass["crashed"] is True
        assert worker.last_pass_at is not None


class TestTheProbeReportsButDoesNotFail:
    def test_readiness_reports_a_disabled_worker(self):
        with TestClient(app) as client:
            payload = client.get("/health/ready").json()

        assert payload["status"] == "ready"
        # The worker is off by default in tests; "off" and "dead" are different
        # answers and the probe distinguishes them.
        assert payload["worker"]["enabled"] is False

    def test_a_stale_worker_does_not_fail_the_probe(self, monkeypatch):
        """503 here would pull a serving instance out of rotation to fix a cron."""
        worker = _worker(poll_seconds=30)
        # Relative to the real clock: the probe reads the heartbeat with no
        # injected `now`, so a fixed timestamp would be stale or fresh
        # depending on what today's date happens to be.
        worker._record_pass({"checked": 0}, datetime.now(timezone.utc) - timedelta(hours=6))

        with TestClient(app) as client:
            client.app.state.sync_worker = worker
            response = client.get("/health/ready")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ready"
        assert body["worker"]["enabled"] is True
        assert body["worker"]["stale"] is True

    def test_broken_heartbeat_reporting_does_not_break_the_probe(self):
        class _Hostile:
            def heartbeat(self, *args, **kwargs):
                raise RuntimeError("nope")

        with TestClient(app) as client:
            client.app.state.sync_worker = _Hostile()
            response = client.get("/health/ready")

        assert response.status_code == 200
        assert response.json()["worker"] == {"enabled": True, "unknown": True}

    def test_worker_status_survives_an_app_with_no_state(self):
        """Readiness is hit before the lifespan runs in plenty of test setups."""

        class _Request:
            class app:  # noqa: N801 - mimicking Starlette's shape
                class state:
                    pass

        assert worker_status(_Request()) == {"enabled": False}


@pytest.fixture(autouse=True)
def _clear_worker_state():
    """Keep a worker planted on app.state from leaking into the next test."""
    yield
    if hasattr(app.state, "sync_worker"):
        app.state.sync_worker = None
