"""Contract tests for the /v1 back-compat alias.

/v1/<path> must hit the same handler as /<path> and return an identical response,
and the unversioned paths must keep working unchanged.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from env.api import app

client = TestClient(app)


def test_v1_health_matches_unversioned() -> None:
    a = client.get("/health")
    b = client.get("/v1/health")
    assert a.status_code == 200
    assert b.status_code == 200
    assert a.json() == b.json()


def test_v1_version_matches_unversioned() -> None:
    a = client.get("/version")
    b = client.get("/v1/version")
    assert a.status_code == 200
    assert b.status_code == 200
    assert a.json() == b.json()


def test_v1_reset_matches_unversioned() -> None:
    payload = {"task_id": "easy_classification", "seed": 42, "persona": "balanced"}
    a = client.post("/reset", json=payload)
    b = client.post("/v1/reset", json=payload)
    assert a.status_code == 200
    assert b.status_code == 200
    assert a.json() == b.json()


def test_unversioned_paths_still_work() -> None:
    # The stability guarantee: the bare paths are unchanged.
    assert client.get("/health").status_code == 200
    assert client.get("/tasks").status_code == 200
