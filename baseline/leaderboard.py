from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from statistics import mean

from baseline.run_baseline import run


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

            rows.append(
                {
                    "task": task,
                    "persona": persona,
                    "avg_score": round(mean(scores), 6),
                    "avg_reward": round(mean(rewards), 6),
                    "avg_steps": round(mean(step_counts), 3),
                    "min_score": round(min(scores), 6),
                    "max_score": round(max(scores), 6),
                }
            )

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
    columns = ["task", "persona", "avg_score", "avg_reward", "avg_steps", "min_score", "max_score"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row[column] for column in columns})


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate baseline leaderboard")
    parser.add_argument("--tasks", default=",".join(DEFAULT_TASKS), help="Comma-separated task ids")
    parser.add_argument("--personas", default=",".join(DEFAULT_PERSONAS), help="Comma-separated persona ids")
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
