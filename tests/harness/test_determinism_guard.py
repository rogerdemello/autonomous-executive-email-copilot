"""Full-matrix determinism guard (INV-2).

Grades the canonical rollout twice for every (task, persona, seed) and asserts
byte-identical ``GraderResponse`` output. Complements the property-based
determinism check with the exact canonical trajectories.
"""

from __future__ import annotations

import pytest

from env.grader import evaluate_trajectory

from .invariants import HARNESS_SEEDS, PERSONAS, TASKS, canonical_trace


@pytest.mark.parametrize("task", TASKS)
@pytest.mark.parametrize("persona", PERSONAS)
@pytest.mark.parametrize("seed", HARNESS_SEEDS)
def test_canonical_rollout_is_deterministic(task: str, persona: str, seed: int) -> None:
    trace = canonical_trace(task, seed, persona)
    first = evaluate_trajectory(task_id=task, seed=seed, actions=trace, persona=persona)
    second = evaluate_trajectory(task_id=task, seed=seed, actions=trace, persona=persona)
    assert first.model_dump() == second.model_dump()
