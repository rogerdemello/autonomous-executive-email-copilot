"""Tests for report-only eval-depth metrics (calibration, cost-aware, credit)."""

from __future__ import annotations

import pytest

from benchmark.agents import BenchmarkMetrics
from benchmark.cost_aware import cost_efficiency, score_per_1k_tokens, score_per_dollar
from benchmark.credit_report import aggregate_step_credit
from benchmark.runner import BenchmarkResult
from env.eval.calibration import brier_score, expected_calibration_error
from env.models import GraderResponse, StepScoreBreakdown

# --- calibration ----------------------------------------------------------


def test_brier_perfect_and_worst() -> None:
    assert brier_score([(1.0, True), (0.0, False)]) == 0.0
    assert brier_score([(1.0, False)]) == 1.0
    assert brier_score([]) == 0.0


def test_ece_perfectly_calibrated_is_zero() -> None:
    assert expected_calibration_error([(1.0, True), (0.0, False)]) == 0.0


def test_ece_miscalibrated_single_bin() -> None:
    # One sample, confidence 0.8, but wrong -> |0 - 0.8| = 0.8.
    assert expected_calibration_error([(0.8, False)]) == pytest.approx(0.8)


# --- cost-aware -----------------------------------------------------------


def test_score_per_dollar_and_tokens() -> None:
    assert score_per_dollar(0.8, 0.004) == pytest.approx(200.0)
    assert score_per_dollar(0.8, 0.0) is None
    assert score_per_1k_tokens(0.8, 2000) == pytest.approx(0.4)
    assert score_per_1k_tokens(0.8, 0) is None


def test_cost_efficiency_per_agent() -> None:
    results = [
        BenchmarkResult(
            "easy_classification",
            "balanced",
            42,
            "llm",
            BenchmarkMetrics(score=0.8, time_ms=1, tokens=2000, cost_usd=0.004),
        ),
        BenchmarkResult(
            "easy_classification",
            "balanced",
            42,
            "baseline",
            BenchmarkMetrics(score=1.0, time_ms=1, tokens=0, cost_usd=0.0),
        ),
    ]
    rows = {r["agent_name"]: r for r in cost_efficiency(results)}
    assert rows["llm"]["score_per_dollar"] == pytest.approx(200.0)
    assert rows["baseline"]["score_per_dollar"] is None


# --- per-step credit ------------------------------------------------------


def _resp(deltas: list[tuple[str, float]]) -> GraderResponse:
    return GraderResponse(
        task_id="easy_classification",
        seed=42,
        persona="balanced",
        score=0.5,
        breakdown={"classification_accuracy": 0.5},
        total_reward=0.5,
        step_breakdown=[
            StepScoreBreakdown(
                step_number=i + 1, action=action, email_id="e1", score_delta=d, reason=""
            )
            for i, (action, d) in enumerate(deltas)
        ],
    )


def test_aggregate_step_credit_groups_by_action() -> None:
    responses = [
        _resp([("classify", 0.2), ("classify", 0.0)]),
        _resp([("reply", 0.5), ("classify", 0.1)]),
    ]
    agg = aggregate_step_credit(responses)
    assert agg["classify"]["n"] == 3
    assert agg["classify"]["total_delta"] == pytest.approx(0.3)
    assert agg["classify"]["mean_delta"] == pytest.approx(0.1)
    assert agg["reply"]["n"] == 1
