from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from statistics import mean, stdev

from baseline.run_baseline import run

# t-distribution critical values for 95% CI (approximate)
_T_CRITICAL = {
    2: 12.706,
    3: 4.303,
    4: 3.182,
    5: 2.776,
    6: 2.571,
    7: 2.447,
    8: 2.365,
    9: 2.306,
    10: 2.262,
    11: 2.228,
    12: 2.201,
    13: 2.179,
    14: 2.160,
    15: 2.145,
}


def _get_t_critical(df: int) -> float:
    """Get t-critical value for 95% CI with given degrees of freedom."""
    if df < 2:
        return 12.706  # fallback for very small samples
    if df in _T_CRITICAL:
        return _T_CRITICAL[df]
    # Approximate for larger df: use z-score as approximation
    return 1.96


def _calc_confidence_interval(scores: list[float]) -> tuple[float, float]:
    """Calculate mean ± 95% CI margin of error."""
    if len(scores) < 2:
        return 0.0, 0.0
    se = stdev(scores) / (len(scores) ** 0.5)
    margin = _get_t_critical(len(scores) - 1) * se
    return round(margin, 6), round(margin, 6)


def _calc_failure_rate(scores: list[float], threshold: float = 0.3) -> float:
    """Calculate percentage of runs where score < threshold."""
    if not scores:
        return 0.0
    failures = sum(1 for s in scores if s < threshold)
    return round(failures / len(scores) * 100, 2)


def _calc_fairness_score(task_rows: list[dict], task: str) -> float:
    """Calculate fairness score for a task: lower variance = higher fairness.

    Returns 100 - (coefficient of variation * 100), clamped to 0-100.
    Higher is better (more fair across personas).
    """
    if not task_rows:
        return 0.0

    scores_for_task = [r["avg_score"] for r in task_rows if r.get("task") == task]
    if len(scores_for_task) < 2:
        return 100.0  # Single persona = perfectly fair by default

    mean_score = mean(scores_for_task)
    if mean_score == 0:
        return 0.0

    cv = stdev(scores_for_task) / mean_score
    fairness = 100 - (cv * 100)
    return round(max(0.0, min(100.0, fairness)), 2)


DEFAULT_TASKS = [
    "easy_classification",
    "medium_prioritization",
    "hard_full_management",
]

DEFAULT_PERSONAS = [
    "strict_ceo",
    "balanced",
    "chill_manager",
]


def parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def build_leaderboard(
    tasks: list[str],
    personas: list[str],
    seeds: list[int],
    max_steps: int,
    mode: str = "baseline",
    stress_rate: float = 0.0,
    csv_out: str | None = None,
) -> dict[str, object]:
    clamped_stress = max(0.0, min(1.0, stress_rate if mode == "stress" else 0.0))
    rows: list[dict[str, object]] = []
    for task in tasks:
        for persona in personas:
            scores: list[float] = []
            rewards: list[float] = []
            step_counts: list[int] = []
            for seed in seeds:
                result = run(
                    task_id=task,
                    seed=seed,
                    max_steps=max_steps,
                    persona=persona,
                    mode=mode,
                    stress_rate=clamped_stress,
                )
                scores.append(float(result["score"]))
                rewards.append(float(result["total_reward"]))
                step_counts.append(int(result["steps"]))

            ci_margin, _ = _calc_confidence_interval(scores)
            failure_rate = _calc_failure_rate(scores)

            rows.append(
                {
                    "task": task,
                    "persona": persona,
                    "avg_score": round(mean(scores), 6),
                    "avg_reward": round(mean(rewards), 6),
                    "avg_steps": round(mean(step_counts), 3),
                    "min_score": round(min(scores), 6),
                    "max_score": round(max(scores), 6),
                    "ci_margin_95": ci_margin,
                    "failure_rate_pct": failure_rate,
                }
            )

    for task in tasks:
        task_rows = [r for r in rows if r["task"] == task]
        fairness = _calc_fairness_score(task_rows, task)
        for row in task_rows:
            row["fairness_score"] = fairness

    rows.sort(key=lambda item: (item["avg_score"], item["avg_reward"]), reverse=True)

    leaderboard = {
        "seeds": seeds,
        "max_steps": max_steps,
        "mode": mode,
        "stress_rate": round(clamped_stress, 6),
        "csv_out": csv_out,
        "rows": rows,
    }

    if csv_out:
        _write_csv(csv_out, rows)

    return leaderboard


def _write_csv(path_str: str, rows: list[dict[str, object]]) -> None:
    path = Path(path_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "task",
        "persona",
        "avg_score",
        "avg_reward",
        "avg_steps",
        "min_score",
        "max_score",
        "ci_margin_95",
        "failure_rate_pct",
        "fairness_score",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row[column] for column in columns})


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate baseline leaderboard")
    parser.add_argument("--tasks", default=",".join(DEFAULT_TASKS), help="Comma-separated task ids")
    parser.add_argument(
        "--personas", default=",".join(DEFAULT_PERSONAS), help="Comma-separated persona ids"
    )
    parser.add_argument("--seeds", default="42,43,44", help="Comma-separated integer seeds")
    parser.add_argument("--max-steps", type=int, default=120)
    parser.add_argument("--mode", default="baseline", choices=["baseline", "stress"])
    parser.add_argument("--stress-rate", type=float, default=0.0)
    parser.add_argument("--csv-out", default=None)
    args = parser.parse_args()

    tasks = parse_csv(args.tasks)
    personas = parse_csv(args.personas)
    seeds = [int(seed.strip()) for seed in args.seeds.split(",") if seed.strip()]

    leaderboard = build_leaderboard(
        tasks=tasks,
        personas=personas,
        seeds=seeds,
        max_steps=max(1, args.max_steps),
        mode=args.mode,
        stress_rate=args.stress_rate,
        csv_out=args.csv_out,
    )
    print(json.dumps(leaderboard, indent=2))


if __name__ == "__main__":
    main()
