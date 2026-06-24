"""Golden-snapshot guard.

Freezes the FULL ``GraderResponse`` (score, breakdown, total_reward, and every
per-step ``step_breakdown`` delta) for the canonical BaselinePolicy rollout over
tasks x personas x seeds. Any drift fails CI.

Re-baselining is deliberate: run ``python scripts/regen_golden.py``, then commit
the regenerated ``snapshots/baseline_golden.json`` in an isolated, reviewed
``chore(golden): rebaseline -- <reason>`` change. See the plan's re-baselining policy.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from env.grader import evaluate_trajectory

from .invariants import canonical_trace

SNAPSHOT = Path(__file__).parent / "snapshots" / "baseline_golden.json"


def _key(task: str, persona: str, seed: int) -> str:
    return f"{task}|{persona}|{seed}"


@pytest.mark.golden
def test_golden_snapshots_match() -> None:
    assert SNAPSHOT.exists(), (
        f"missing {SNAPSHOT}; run `python scripts/regen_golden.py` to create it"
    )
    expected: dict[str, dict] = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert expected, "snapshot file is empty"

    for key, exp in expected.items():
        task, persona, seed_str = key.split("|")
        seed = int(seed_str)
        trace = canonical_trace(task, seed, persona)
        actual = evaluate_trajectory(
            task_id=task, seed=seed, actions=trace, persona=persona
        ).model_dump()
        assert actual == exp, (
            f"golden drift for {key}. If intentional, rerun scripts/regen_golden.py "
            f"and review the diff line-by-line."
        )
