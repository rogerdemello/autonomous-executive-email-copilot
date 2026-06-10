"""Tests for deterministic agent significance testing (benchmark/significance.py)."""

from __future__ import annotations

import pytest

from benchmark.agents import BenchmarkMetrics
from benchmark.runner import BenchmarkResult
from benchmark.significance import (
    compare_agents,
    compare_all_pairs,
    paired_deltas,
    paired_t_test,
)

_CELLS = [
    ("easy_classification", "balanced", 42),
    ("easy_classification", "balanced", 43),
    ("easy_classification", "balanced", 44),
    ("medium_prioritization", "balanced", 42),
]


def _results(agent: str, scores: list[float]) -> list[BenchmarkResult]:
    return [
        BenchmarkResult(
            task_id=task,
            persona=persona,
            seed=seed,
            agent_name=agent,
            metrics=BenchmarkMetrics(score=score, time_ms=1, tokens=0, cost_usd=0.0),
        )
        for (task, persona, seed), score in zip(_CELLS, scores, strict=True)
    ]


def test_paired_deltas_align_on_shared_cells() -> None:
    a = _results("a", [0.8, 0.8, 0.8, 0.8])
    b = _results("b", [0.5, 0.6, 0.7, 0.8])
    deltas = paired_deltas(a, b)
    assert len(deltas) == 4
    assert deltas == pytest.approx([0.3, 0.2, 0.1, 0.0])


def test_consistent_separation_is_significant() -> None:
    a = _results("a", [0.9, 0.9, 0.9, 0.9])
    b = _results("b", [0.5, 0.5, 0.5, 0.5])  # constant +0.4 delta, zero variance
    out = compare_agents(a + b, "a", "b")
    assert out["p_value"] == 0.0
    assert out["significant"] is True
    assert out["mean_delta"] > 0


def test_zero_difference_is_not_significant() -> None:
    a = _results("a", [0.6, 0.7, 0.8, 0.9])
    b = _results("b", [0.6, 0.7, 0.8, 0.9])
    out = compare_agents(a + b, "a", "b")
    assert out["p_value"] == 1.0
    assert out["significant"] is False
    assert out["mean_delta"] == 0.0


def test_pvalue_in_unit_range_and_deterministic() -> None:
    a = _results("a", [0.7, 0.9, 0.6, 0.85])
    b = _results("b", [0.5, 0.55, 0.62, 0.4])
    first = compare_agents(a + b, "a", "b")
    second = compare_agents(a + b, "a", "b")
    assert first == second  # deterministic (no RNG)
    assert 0.0 <= first["p_value"] <= 1.0


def test_comparison_is_symmetric() -> None:
    a = _results("a", [0.7, 0.9, 0.6, 0.85])
    b = _results("b", [0.5, 0.55, 0.62, 0.4])
    ab = compare_agents(a + b, "a", "b")
    ba = compare_agents(a + b, "b", "a")
    assert abs(ab["mean_delta"] + ba["mean_delta"]) < 1e-9
    assert ab["p_value"] == ba["p_value"]
    assert abs(ab["cohens_d"] + ba["cohens_d"]) < 1e-9


def test_small_sample_guard() -> None:
    assert paired_t_test([]) == {"t_stat": 0.0, "p_value": 1.0}
    assert paired_t_test([0.1]) == {"t_stat": 0.0, "p_value": 1.0}


def test_compare_all_pairs_covers_unique_pairs() -> None:
    results = (
        _results("a", [0.9, 0.9, 0.9, 0.9])
        + _results("b", [0.5, 0.5, 0.5, 0.5])
        + _results("c", [0.7, 0.7, 0.7, 0.7])
    )
    pairs = compare_all_pairs(results)
    names = {(p["agent_a"], p["agent_b"]) for p in pairs}
    assert names == {("a", "b"), ("a", "c"), ("b", "c")}
