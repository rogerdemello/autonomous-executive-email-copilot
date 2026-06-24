#!/usr/bin/env python3
"""Regenerate the golden snapshot used by tests/harness/test_golden_snapshots.py.

This is NEVER run automatically in CI. Run it deliberately when a grader/scenario
change is intended, then commit the regenerated snapshot in an isolated, reviewed
`chore(golden): rebaseline -- <reason>` change so every score movement is visible.

Usage:
    python scripts/regen_golden.py
"""

from __future__ import annotations

import json
from pathlib import Path

from env.environment import ExecutiveEmailEnv
from env.grader import evaluate_trajectory
from env.policy import BaselinePolicy

TASKS = ["easy_classification", "medium_prioritization", "hard_full_management"]
PERSONAS = ["strict_ceo", "balanced", "chill_manager"]
SEEDS = [42, 43]

SNAPSHOT = (
    Path(__file__).resolve().parents[1] / "tests" / "harness" / "snapshots" / "baseline_golden.json"
)


def _canonical_trace(task_id: str, seed: int, persona: str, max_steps: int = 100) -> list:
    env = ExecutiveEmailEnv(task_id=task_id, seed=seed, persona=persona)
    observation = env.reset(task_id=task_id, seed=seed, persona=persona)
    policy = BaselinePolicy()
    trace = []
    for _ in range(max(1, max_steps)):
        action = policy.next_action(observation)
        if action is None:
            break
        trace.append(action)
        result = env.step(action)
        observation = result.observation
        if result.done:
            break
    return trace


def main() -> None:
    out: dict[str, dict] = {}
    for task in TASKS:
        for persona in PERSONAS:
            for seed in SEEDS:
                trace = _canonical_trace(task, seed, persona)
                resp = evaluate_trajectory(task_id=task, seed=seed, actions=trace, persona=persona)
                out[f"{task}|{persona}|{seed}"] = resp.model_dump()

    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {len(out)} snapshots to {SNAPSHOT}")


if __name__ == "__main__":
    main()
