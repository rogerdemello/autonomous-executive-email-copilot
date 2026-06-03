from __future__ import annotations

import csv
import json
import os
from statistics import mean, stdev

from benchmark.agents import BenchmarkMetrics
from benchmark.results_report import (
    aggregate_results,
    write_results_report,
)
from benchmark.runner import BenchmarkResult


def _result(
    task_id: str,
    persona: str,
    agent_name: str,
    seed: int,
    score: float,
    tokens: int = 0,
    cost_usd: float = 0.0,
    time_ms: int = 10,
) -> BenchmarkResult:
    return BenchmarkResult(
        task_id=task_id,
        persona=persona,
        seed=seed,
        agent_name=agent_name,
        metrics=BenchmarkMetrics(
            score=score,
            time_ms=time_ms,
            tokens=tokens,
            cost_usd=cost_usd,
        ),
    )


def test_aggregate_mean_and_ci_multi_seed():
    scores = [0.2, 0.5, 0.8]
    results = [
        _result("easy_classification", "balanced", "baseline", seed, score)
        for seed, score in zip([42, 43, 44], scores, strict=True)
    ]

    rows = aggregate_results(results)

    assert len(rows) == 1
    row = rows[0]
    assert row["n"] == 3
    assert row["mean_score"] == round(mean(scores), 6)

    half_width = 1.96 * stdev(scores) / (len(scores) ** 0.5)
    assert row["ci95_low"] == round(mean(scores) - half_width, 6)
    assert row["ci95_high"] == round(mean(scores) + half_width, 6)


def test_aggregate_single_seed_collapses_ci():
    results = [_result("easy_classification", "balanced", "baseline", 42, 0.6)]

    rows = aggregate_results(results)

    assert len(rows) == 1
    row = rows[0]
    assert row["n"] == 1
    assert row["mean_score"] == 0.6
    # With one sample the interval has zero half-width.
    assert row["ci95_low"] == 0.6
    assert row["ci95_high"] == 0.6


def test_aggregate_groups_by_task_persona_agent():
    results = [
        _result("easy_classification", "balanced", "baseline", 42, 0.5),
        _result("easy_classification", "balanced", "baseline", 43, 0.7),
        _result("easy_classification", "balanced", "multiagent", 42, 0.1),
        _result("medium_prioritization", "strict_ceo", "baseline", 42, 0.9),
    ]

    rows = aggregate_results(results)

    keys = {(r["task_id"], r["persona"], r["agent_name"]) for r in rows}
    assert keys == {
        ("easy_classification", "balanced", "baseline"),
        ("easy_classification", "balanced", "multiagent"),
        ("medium_prioritization", "strict_ceo", "baseline"),
    }

    baseline_easy = next(
        r
        for r in rows
        if r["task_id"] == "easy_classification" and r["agent_name"] == "baseline"
    )
    assert baseline_easy["n"] == 2
    assert baseline_easy["mean_score"] == round(mean([0.5, 0.7]), 6)


def test_aggregate_means_tokens_cost_time():
    results = [
        _result("hard_full_management", "balanced", "llm", 42, 0.5, tokens=100, cost_usd=0.01, time_ms=200),
        _result("hard_full_management", "balanced", "llm", 43, 0.5, tokens=300, cost_usd=0.03, time_ms=400),
    ]

    rows = aggregate_results(results)
    row = rows[0]

    assert row["mean_tokens"] == 200.0
    assert row["mean_cost_usd"] == 0.02
    assert row["mean_time_ms"] == 300.0


def test_write_results_report_produces_three_files(tmp_path):
    results = [
        _result("easy_classification", "balanced", "baseline", 42, 0.5),
        _result("easy_classification", "balanced", "baseline", 43, 0.7),
        _result("easy_classification", "balanced", "multiagent", 42, 0.1),
    ]

    out_dir = os.path.join(str(tmp_path), "out")
    artifacts = write_results_report(results, out_dir)

    for name in ("json", "csv", "html"):
        assert os.path.exists(artifacts[name]), f"missing {name} artifact"

    assert os.path.exists(os.path.join(out_dir, "results.json"))
    assert os.path.exists(os.path.join(out_dir, "results.csv"))
    assert os.path.exists(os.path.join(out_dir, "results.html"))


def test_results_json_contains_aggregates_and_raw(tmp_path):
    results = [
        _result("easy_classification", "balanced", "baseline", 42, 0.5),
        _result("easy_classification", "balanced", "baseline", 43, 0.7),
    ]

    write_results_report(results, str(tmp_path))

    with open(os.path.join(str(tmp_path), "results.json"), encoding="utf-8") as handle:
        payload = json.load(handle)

    assert "aggregates" in payload
    assert "results" in payload
    assert len(payload["results"]) == 2
    assert len(payload["aggregates"]) == 1
    assert payload["aggregates"][0]["mean_score"] == round(mean([0.5, 0.7]), 6)


def test_results_csv_has_header_and_rows(tmp_path):
    results = [
        _result("easy_classification", "balanced", "baseline", 42, 0.5),
        _result("medium_prioritization", "strict_ceo", "multiagent", 42, 0.2),
    ]

    write_results_report(results, str(tmp_path))

    with open(os.path.join(str(tmp_path), "results.csv"), encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 2
    assert "mean_score" in rows[0]
    assert "ci95_low" in rows[0]
    assert "ci95_high" in rows[0]


def test_results_html_is_standalone_table(tmp_path):
    results = [_result("easy_classification", "balanced", "baseline", 42, 0.5)]

    write_results_report(results, str(tmp_path))

    with open(os.path.join(str(tmp_path), "results.html"), encoding="utf-8") as handle:
        html = handle.read()

    assert "<!DOCTYPE html>" in html
    assert "<table>" in html
    assert "<style>" in html
    assert "mean_score" in html
