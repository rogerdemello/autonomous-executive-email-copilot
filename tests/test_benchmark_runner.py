from __future__ import annotations

import pytest

from research.benchmark.agents import BaseBenchmarkAgent, BenchmarkMetrics
from research.benchmark.runner import BenchmarkResult, BenchmarkRunner


class _StubAgent(BaseBenchmarkAgent):
    name: str = "stub"

    def run(self, task_id, seed, persona, max_steps=100):
        return BenchmarkMetrics(
            score=0.75,
            time_ms=50,
            tokens=100,
            cost_usd=0.002,
            safety_score=1.0,
        )


@pytest.fixture
def stub_runner():
    runner = BenchmarkRunner(
        tasks=["easy_classification"],
        personas=["balanced"],
        seeds=[42],
    )
    return runner


def test_benchmark_result_to_dict():
    metrics = BenchmarkMetrics(
        score=0.85, time_ms=150, tokens=500, cost_usd=0.001, safety_score=0.9
    )
    result = BenchmarkResult(
        task_id="easy_classification",
        persona="balanced",
        seed=42,
        agent_name="baseline",
        metrics=metrics,
    )
    d = result.to_dict()
    assert d["task_id"] == "easy_classification"
    assert d["persona"] == "balanced"
    assert d["seed"] == 42
    assert d["agent_name"] == "baseline"
    assert d["score"] == 0.85
    assert d["safety_score"] == 0.9
    assert d["time_ms"] == 150
    assert d["tokens"] == 500
    assert d["cost_usd"] == 0.001
    assert len(d) == 9


def test_run_agent_baseline_returns_benchmark_results(monkeypatch, stub_runner):
    stub = _StubAgent()
    monkeypatch.setattr(stub_runner, "baseline_agent", stub)

    results = stub_runner.run_agent("baseline")

    assert len(results) == 1
    assert all(isinstance(r, BenchmarkResult) for r in results)
    assert results[0].agent_name == "baseline"
    assert results[0].task_id == "easy_classification"
    assert results[0].persona == "balanced"
    assert results[0].seed == 42
    assert results[0].metrics.score == 0.75


def test_run_agent_llm_returns_results(monkeypatch, stub_runner):
    stub = _StubAgent()
    stub.name = "llm"
    monkeypatch.setattr(stub_runner, "llm_agent", stub)

    results = stub_runner.run_agent("llm")

    assert len(results) == 1
    assert results[0].agent_name == "llm"
    assert results[0].metrics.tokens == 100


def test_run_agent_multiagent_works(monkeypatch, stub_runner):
    stub = _StubAgent()
    stub.name = "multiagent"
    monkeypatch.setattr(stub_runner, "multiagent", stub)

    results = stub_runner.run_agent("multiagent")

    assert len(results) == 1
    assert results[0].agent_name == "multiagent"


def test_run_agent_reflective_works(monkeypatch, stub_runner):
    stub = _StubAgent()
    stub.name = "reflective"
    monkeypatch.setattr(stub_runner, "reflective", stub)

    results = stub_runner.run_agent("reflective")

    assert len(results) == 1
    assert results[0].agent_name == "reflective"


def test_run_agent_unknown_raises_value_error(stub_runner):
    with pytest.raises(ValueError, match="Unknown agent: unknown"):
        stub_runner.run_agent("unknown")


def test_run_all_returns_results_from_all_agents(monkeypatch):
    runner = BenchmarkRunner(
        tasks=["easy_classification"],
        personas=["balanced"],
        seeds=[42],
    )
    stub_baseline = _StubAgent()
    stub_baseline.name = "baseline"
    stub_reflective = _StubAgent()
    stub_reflective.name = "reflective"
    stub_multi = _StubAgent()
    stub_multi.name = "multiagent"
    monkeypatch.setattr(runner, "baseline_agent", stub_baseline)
    monkeypatch.setattr(runner, "reflective", stub_reflective)
    monkeypatch.setattr(runner, "multiagent", stub_multi)

    results = runner.run_all()

    # run_all is the no-credentials smoke path: every offline agent, and
    # never the key-requiring llm agent (that one is opt-in via run_agent).
    assert len(results) == 3
    agent_names = {r.agent_name for r in results}
    assert agent_names == {"baseline", "reflective", "multiagent"}


def test_run_all_with_custom_config_respects_dimensions(monkeypatch):
    stub = _StubAgent()
    runner = BenchmarkRunner(
        tasks=["easy_classification", "medium_prioritization"],
        personas=["balanced", "strict_ceo"],
        seeds=[42, 43],
    )
    monkeypatch.setattr(runner, "baseline_agent", stub)
    monkeypatch.setattr(runner, "reflective", stub)
    monkeypatch.setattr(runner, "multiagent", stub)

    results = runner.run_all()

    expected_count = 2 * 2 * 2 * 3  # tasks * personas * seeds * agents
    assert len(results) == expected_count

    unique_tasks = {r.task_id for r in results}
    unique_personas = {r.persona for r in results}
    unique_seeds = {r.seed for r in results}
    assert unique_tasks == {"easy_classification", "medium_prioritization"}
    assert unique_personas == {"balanced", "strict_ceo"}
    assert unique_seeds == {42, 43}


def test_benchmark_result_to_dict_round_trip():
    metrics = BenchmarkMetrics(score=0.5, time_ms=200, tokens=999, cost_usd=0.05, safety_score=0.8)
    result = BenchmarkResult(
        task_id="hard_full_management",
        persona="chill_manager",
        seed=99,
        agent_name="llm",
        metrics=metrics,
    )
    d = result.to_dict()
    assert isinstance(d, dict)
    assert all(
        k in d
        for k in (
            "task_id",
            "persona",
            "seed",
            "agent_name",
            "score",
            "safety_score",
            "time_ms",
            "tokens",
            "cost_usd",
        )
    )
