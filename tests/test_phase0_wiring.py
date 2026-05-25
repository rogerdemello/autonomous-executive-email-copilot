"""Phase 0 wiring tests: prove the documented features are actually connected.

These cover gaps that were previously listed as "exists but not wired":
- baseline runs persist episodes to the DB (not just in-memory)
- high-scoring runs auto-save a learning trajectory
- API requests increment telemetry metrics
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from env.api import app, episode_history_store
from env.repositories import EpisodeRepository
from env.learning.trajectory_store import trajectory_store

client = TestClient(app)


def _run_baseline(task_id: str = "easy_classification", seed: int = 42, persona: str = "balanced") -> dict:
    response = client.post(
        "/baseline",
        json={
            "task_id": task_id,
            "seed": seed,
            "persona": persona,
            "mode": "baseline",
            "max_steps": 100,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_baseline_persists_episode_to_db() -> None:
    result = _run_baseline()
    episode_id = f"{result['task_id']}_{result['seed']}_{result['persona']}"

    # A fresh repository instance must see the persisted episode (i.e. it is in
    # the DB, not only the in-process history store).
    fresh_repo = EpisodeRepository()
    episode = fresh_repo.get_episode(episode_id=episode_id)
    assert episode is not None
    assert episode.task_id == result["task_id"]
    assert abs(episode.score - result["score"]) < 1e-6


def test_replay_falls_back_to_db_after_memory_cleared() -> None:
    result = _run_baseline()
    episode_id = f"{result['task_id']}_{result['seed']}_{result['persona']}"

    # Simulate a restart: drop the in-memory history and confirm replay still works.
    episode_history_store.pop(episode_id, None)

    response = client.get(f"/replay/{episode_id}")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["episode_id"] == episode_id
    assert payload["task_id"] == result["task_id"]


def test_high_score_baseline_stores_trajectory() -> None:
    # easy_classification / seed 42 / balanced scores well above the 0.7 store
    # threshold, so a learning trajectory should be captured.
    result = _run_baseline(task_id="easy_classification", seed=42, persona="balanced")
    assert result["score"] >= trajectory_store.SCORE_THRESHOLD
    episode_id = f"{result['task_id']}_{result['seed']}_{result['persona']}"

    trajectories = trajectory_store.get_trajectories(
        task_id="easy_classification", persona="balanced"
    )
    assert any(t["episode_id"] == episode_id for t in trajectories)


def test_requests_increment_telemetry() -> None:
    client.get("/health")
    client.get("/tasks")

    response = client.get("/metrics")
    assert response.status_code == 200
    body = response.text
    assert "requests_total" in body
    assert "request_duration_ms" in body
