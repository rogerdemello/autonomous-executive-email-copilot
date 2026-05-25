from __future__ import annotations

import json

from benchmark.agents import BaselineAgent, BenchmarkMetrics, LLMAgent, MultiAgent
from benchmark.reporter import Reporter
from benchmark.runner import DEFAULT_PERSONAS, DEFAULT_SEEDS, DEFAULT_TASKS, BenchmarkRunner


def test_default_tasks():
    assert DEFAULT_TASKS == ["easy_classification", "medium_prioritization", "hard_full_management"]


def test_default_personas():
    assert DEFAULT_PERSONAS == ["strict_ceo", "balanced", "chill_manager"]


def test_default_seeds():
    assert DEFAULT_SEEDS == [42, 43, 44]


def test_benchmark_runner_init():
    runner = BenchmarkRunner()
    assert runner.tasks == DEFAULT_TASKS
    assert runner.personas == DEFAULT_PERSONAS
    assert runner.seeds == DEFAULT_SEEDS


def test_benchmark_runner_custom_config():
    runner = BenchmarkRunner(
        tasks=["easy_classification"],
        personas=["balanced"],
        seeds=[42],
    )
    assert runner.tasks == ["easy_classification"]
    assert runner.personas == ["balanced"]
    assert runner.seeds == [42]


def test_baseline_agent_name():
    agent = BaselineAgent()
    assert agent.name == "baseline"


def test_llm_agent_name():
    agent = LLMAgent()
    assert agent.name == "llm"


def test_multi_agent_name():
    agent = MultiAgent()
    assert agent.name == "multiagent"


def test_benchmark_metrics_to_dict():
    metrics = BenchmarkMetrics(score=0.85, time_ms=150, tokens=500, cost_usd=0.001)
    data = metrics.to_dict()
    assert data["score"] == 0.85
    assert data["time_ms"] == 150
    assert data["tokens"] == 500
    assert data["cost_usd"] == 0.001


def test_reporter_json_output():
    runner = BenchmarkRunner(
        tasks=["easy_classification"],
        personas=["balanced"],
        seeds=[42],
    )
    results = runner.run_all()
    reporter = Reporter(runner)
    json_output = reporter.generate_json(results)
    data = json.loads(json_output)
    assert "summary" in data
    assert "results" in data
    assert len(data["results"]) == 3


def test_reporter_html_output():
    runner = BenchmarkRunner(
        tasks=["easy_classification"],
        personas=["balanced"],
        seeds=[42],
    )
    results = runner.run_all()
    reporter = Reporter(runner)
    html_output = reporter.generate_html(results)
    assert "<html>" in html_output.lower()
    assert "Agent Benchmark Comparison" in html_output
