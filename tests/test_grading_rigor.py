"""Grading-transform properties, golden-score regression, and hybrid mode.

These lock in the validity of the scoring transforms (bounds + monotonicity)
and guard against accidental drift in baseline scores.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from baseline.run_baseline import run
from env.api import app
from env.grader import _compute_score, _normalize_reward
from env.utils import strict_unit_interval

client = TestClient(app)


# --- Transform properties (open interval (0,1) + monotonicity) ---

_SWEEP = [-1000, -50, -5, -1, -0.25, 0, 0.25, 1, 5, 50, 1000]


@pytest.mark.parametrize("value", [*_SWEEP, 0.5, 0.999999, 1.0, 2.0])
def test_strict_unit_interval_is_open_unit(value):
    out = strict_unit_interval(value)
    assert 0.0 < out < 1.0


def test_strict_unit_interval_monotonic():
    xs = [-2, -1, -0.5, 0, 0.3, 0.5, 0.7, 1.0, 2.0]
    ys = [strict_unit_interval(x) for x in xs]
    assert all(a <= b for a, b in zip(ys, ys[1:], strict=False))


def test_normalize_reward_open_unit_and_midpoint():
    assert abs(_normalize_reward(0.0) - 0.5) < 1e-9
    for v in _SWEEP:
        assert 0.0 < _normalize_reward(v) < 1.0


def test_normalize_reward_strictly_increasing():
    xs = [-50, -10, -1, -0.1, 0, 0.1, 1, 10, 50]
    ys = [_normalize_reward(x) for x in xs]
    assert all(a < b for a, b in zip(ys, ys[1:], strict=False))


def test_compute_score_weights_hard_task():
    metrics = {
        "classification_accuracy": 1.0,
        "action_correctness": 0.5,
        "response_quality": 0.0,
        "prioritization": 0.0,
        "resolved_ratio": 0.0,
    }
    # 0.3*1.0 + 0.3*0.5 + 0.4*0.0 = 0.45
    assert abs(_compute_score(metrics, "hard_full_management") - 0.45) < 1e-9


# --- Golden-score regression + determinism ---

GOLDEN = {
    "easy_classification": (0.999999, 13),
    "medium_prioritization": (0.999999, 14),
    "hard_full_management": (0.600455, 26),
}


@pytest.mark.parametrize("task,expected", GOLDEN.items())
def test_baseline_golden_scores(task, expected):
    result = run(task_id=task, seed=42, max_steps=100, persona="balanced", mode="baseline")
    expected_score, expected_steps = expected
    assert abs(result["score"] - expected_score) < 1e-4, result["score"]
    assert result["steps"] == expected_steps


@pytest.mark.parametrize("task", list(GOLDEN))
def test_baseline_is_deterministic(task):
    a = run(task_id=task, seed=42, max_steps=100, persona="balanced", mode="baseline")
    b = run(task_id=task, seed=42, max_steps=100, persona="balanced", mode="baseline")
    assert a["score"] == b["score"]
    assert a["total_reward"] == b["total_reward"]
    assert a["steps"] == b["steps"]


# --- /baseline accepts hybrid mode ---


def test_baseline_endpoint_accepts_hybrid():
    resp = client.post(
        "/baseline",
        json={
            "task_id": "easy_classification",
            "seed": 42,
            "persona": "balanced",
            "mode": "hybrid",
            "max_steps": 40,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["mode"] == "hybrid"
