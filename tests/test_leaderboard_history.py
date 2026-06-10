"""Tests for persisted leaderboard history (benchmark/history.py)."""

from __future__ import annotations

from benchmark.agents import BenchmarkMetrics
from benchmark.history import append_run, compute_deltas, load_history, summarize_results
from benchmark.runner import BenchmarkResult


def _results(
    agent: str, score: float, safety: float = 1.0, cost: float = 0.0
) -> list[BenchmarkResult]:
    return [
        BenchmarkResult(
            task_id="easy_classification",
            persona="balanced",
            seed=seed,
            agent_name=agent,
            metrics=BenchmarkMetrics(
                score=score, time_ms=1, tokens=0, cost_usd=cost, safety_score=safety
            ),
        )
        for seed in (42, 43)
    ]


def test_summarize_results_means_per_agent() -> None:
    results = _results("baseline", 0.8, safety=0.9, cost=0.0)
    summary = summarize_results(results)
    assert summary["baseline"]["mean_score"] == 0.8
    assert summary["baseline"]["mean_safety_score"] == 0.9
    assert summary["baseline"]["n"] == 2


def test_first_append_has_no_deltas(tmp_path) -> None:
    path = tmp_path / "history.jsonl"
    entry = append_run(path, _results("baseline", 0.8), timestamp="t0")
    assert entry["deltas"]["baseline"] is None
    assert load_history(path) == [entry]


def test_second_append_computes_deltas_and_preserves_prior(tmp_path) -> None:
    path = tmp_path / "history.jsonl"
    first = append_run(path, _results("baseline", 0.8), timestamp="t0")
    second = append_run(path, _results("baseline", 0.9), timestamp="t1")

    history = load_history(path)
    assert len(history) == 2
    assert history[0] == first  # prior line untouched
    assert second["deltas"]["baseline"]["mean_score"] == round(0.9 - 0.8, 6)


def test_identical_rerun_has_zero_delta(tmp_path) -> None:
    path = tmp_path / "history.jsonl"
    append_run(path, _results("baseline", 0.8), timestamp="t0")
    second = append_run(path, _results("baseline", 0.8), timestamp="t1")
    assert second["deltas"]["baseline"] == {
        "mean_score": 0.0,
        "mean_safety_score": 0.0,
        "mean_cost_usd": 0.0,
    }


def test_compute_deltas_handles_missing_previous() -> None:
    summary = {"a": {"mean_score": 0.5, "mean_safety_score": 1.0, "mean_cost_usd": 0.0}}
    assert compute_deltas(summary, None)["a"] is None
