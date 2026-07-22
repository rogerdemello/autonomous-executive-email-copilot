from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from statistics import mean, stdev

from benchmark.agents import LLMAgent as BenchmarkLLMAgent
from benchmark.runner import BenchmarkResult


@dataclass
class ABConfig:
    name: str
    model: str
    provider: str = "openai"

    @classmethod
    def from_cli(cls, label: str, raw: str) -> ABConfig:
        parts = raw.split(",", 1)
        model = parts[0].strip()
        provider = parts[1].strip() if len(parts) > 1 else "openai"
        return cls(name=label, model=model, provider=provider)


@dataclass
class Comparison:
    a_name: str
    b_name: str
    metric: str
    a_values: list[float]
    b_values: list[float]
    a_mean: float
    b_mean: float
    delta: float
    delta_pct: float


def _run_config(
    config: ABConfig,
    tasks: list[str],
    personas: list[str],
    seeds: list[int],
) -> list[BenchmarkResult]:
    """Run the full benchmark grid for a single config."""
    print(f"\n  Running {config.name} (model={config.model}, provider={config.provider})...")
    agent = BenchmarkLLMAgent(model=config.model)
    results: list[BenchmarkResult] = []

    total = len(tasks) * len(personas) * len(seeds)
    for i, task_id in enumerate(tasks):
        for persona in personas:
            for seed in seeds:
                metrics = agent.run(
                    task_id=task_id,
                    seed=seed,
                    persona=persona,
                    max_steps=100,
                )
                results.append(
                    BenchmarkResult(
                        task_id=task_id,
                        persona=persona,
                        seed=seed,
                        agent_name=config.name,
                        metrics=metrics,
                    )
                )
                print(
                    f"    [{i + 1}/{total}] {task_id}/{persona}/seed={seed}: "
                    f"score={metrics.score:.4f}, cost=${metrics.cost_usd:.4f}, "
                    f"latency={metrics.time_ms:.0f}ms"
                )
    return results


def _compare(
    results_a: list[BenchmarkResult], results_b: list[BenchmarkResult]
) -> list[Comparison]:
    """Compute per-metric comparisons between two result sets."""
    metrics = ["score", "safety_score", "cost_usd", "time_ms", "tokens"]
    comparisons: list[Comparison] = []

    for metric in metrics:
        a_vals = [getattr(r.metrics, metric) for r in results_a]
        b_vals = [getattr(r.metrics, metric) for r in results_b]

        a_mean_val = mean(a_vals)
        b_mean_val = mean(b_vals)
        delta = b_mean_val - a_mean_val
        delta_pct = (delta / a_mean_val * 100.0) if a_mean_val != 0 else 0.0

        comparisons.append(
            Comparison(
                a_name=results_a[0].agent_name,
                b_name=results_b[0].agent_name,
                metric=metric,
                a_values=a_vals,
                b_values=b_vals,
                a_mean=a_mean_val,
                b_mean=b_mean_val,
                delta=delta,
                delta_pct=delta_pct,
            )
        )
    return comparisons


def _print_comparison(comparisons: list[Comparison]) -> None:
    """Print a human-readable comparison table."""
    print("\n" + "=" * 80)
    print("  A/B Comparison Report")
    print("=" * 80)

    for comp in comparisons:
        a_name = comp.a_name
        b_name = comp.b_name
        metric = comp.metric.replace("_", " ").title()

        if len(comp.a_values) > 1:
            a_std = stdev(comp.a_values)
            b_std = stdev(comp.b_values)
            a_str = f"{comp.a_mean:.4f} \u00b1 {a_std:.4f}"
            b_str = f"{comp.b_mean:.4f} \u00b1 {b_std:.4f}"
        else:
            a_str = f"{comp.a_mean:.4f}"
            b_str = f"{comp.b_mean:.4f}"

        winner = a_name if comp.delta < 0 else b_name
        if comp.metric in ("safety_score", "score"):
            winner = a_name if comp.delta > 0 else b_name

        print(f"\n  {metric}:")
        print(f"    {a_name:20s}  {a_str}")
        print(f"    {b_name:20s}  {b_str}")
        print(f"    Delta:          {comp.delta:+.4f} ({comp.delta_pct:+.2f}%)")
        if comp.metric not in ("tokens", "cost_usd"):
            print(f"    Better:         {winner}")


def _save_results(
    results_a: list[BenchmarkResult],
    results_b: list[BenchmarkResult],
    output: str,
) -> None:
    """Save raw result dicts to a JSON file."""
    data = {
        "a": [r.to_dict() for r in results_a],
        "b": [r.to_dict() for r in results_b],
    }
    with open(output, "w") as f:
        json.dump(data, f, indent=2)
    print(f"\n  Raw results saved to {output}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="A/B evaluation: compare two LLM provider/model configs"
    )
    parser.add_argument(
        "--a",
        required=True,
        help='Config A: "model[,provider]" (e.g. "gpt-4o,openai")',
    )
    parser.add_argument(
        "--b",
        required=True,
        help='Config B: "model[,provider]" (e.g. "claude-3.5-sonnet,anthropic")',
    )
    parser.add_argument(
        "--tasks",
        nargs="*",
        default=["easy_classification", "medium_prioritization", "hard_full_management"],
        help="Tasks to eval (default: all three)",
    )
    parser.add_argument(
        "--personas",
        nargs="*",
        default=["strict_ceo", "balanced", "chill_manager"],
        help="Personas (default: all three)",
    )
    parser.add_argument(
        "--seeds",
        type=int,
        nargs="*",
        default=[42, 43, 44, 45],
        help="Seeds (default: 42-45 for fast runs)",
    )
    parser.add_argument("--output", "-o", default="ab_eval_results.json", help="Output JSON path")

    args = parser.parse_args()
    config_a = ABConfig.from_cli("A", args.a)
    config_b = ABConfig.from_cli("B", args.b)

    start = time.perf_counter()

    results_a = _run_config(config_a, args.tasks, args.personas, args.seeds)
    results_b = _run_config(config_b, args.tasks, args.personas, args.seeds)

    comparisons = _compare(results_a, results_b)
    _print_comparison(comparisons)
    _save_results(results_a, results_b, args.output)

    elapsed = time.perf_counter() - start
    print(f"\n  Total time: {elapsed:.1f}s")
    print(f"  Episodes run: {len(results_a) + len(results_b)}")


if __name__ == "__main__":
    main()
