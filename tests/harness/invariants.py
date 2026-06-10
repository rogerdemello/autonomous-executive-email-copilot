"""Reusable invariant assertions + canonical rollout helpers.

Every later advancement imports from here so the invariant contract is defined
exactly once. Assertions mirror the production transforms in ``env/utils.py``
(``strict_unit_interval``, ``epsilon=1e-6``) and ``env/grader.py``.
"""

from __future__ import annotations

import math

from env.environment import ExecutiveEmailEnv
from env.grader import evaluate_trajectory
from env.models import Action, GraderResponse
from env.policy import BaselinePolicy

TASKS: list[str] = [
    "easy_classification",
    "medium_prioritization",
    "hard_full_management",
]
PERSONAS: list[str] = ["strict_ceo", "balanced", "chill_manager"]
HARNESS_SEEDS: list[int] = [42, 43, 44]
EPSILON = 1e-6


# --- INV-1 / INV-4 scalar guards -----------------------------------------


def assert_open_unit_interval(value: float, *, name: str = "value") -> None:
    """INV-1: ``value`` must be strictly inside the open interval (0, 1)."""
    assert isinstance(value, (int, float)), f"{name} is not numeric: {value!r}"
    assert math.isfinite(value), f"{name} is not finite: {value}"
    assert 0.0 < value < 1.0, f"{name} outside open interval (0,1): {value}"


def assert_reward_bounded(reward: float, *, name: str = "reward") -> None:
    """INV-4: a per-step reward must be finite and within [-1, 1]."""
    assert math.isfinite(reward), f"{name} is not finite: {reward}"
    assert -1.0 <= reward <= 1.0, f"{name} outside [-1, 1]: {reward}"


def assert_grader_response_valid(resp: GraderResponse) -> None:
    """INV-1: every grader-facing score is in (0, 1); step deltas are 6dp-finite."""
    assert_open_unit_interval(resp.score, name="score")
    assert_open_unit_interval(resp.total_reward, name="total_reward")
    assert resp.breakdown, "breakdown must not be empty"
    for key, val in resp.breakdown.items():
        assert_open_unit_interval(val, name=f"breakdown[{key}]")
    for sb in resp.step_breakdown:
        assert math.isfinite(sb.score_delta), f"step {sb.step_number} delta not finite"
        assert round(sb.score_delta, 6) == sb.score_delta, (
            f"step {sb.step_number} delta not rounded to 6dp: {sb.score_delta}"
        )


# --- INV-2 / INV-3 trajectory guards -------------------------------------


def assert_deterministic(
    task_id: str, seed: int, persona: str, actions: list[Action]
) -> GraderResponse:
    """INV-2: grading the same (task, seed, persona, actions) twice is identical."""
    first = evaluate_trajectory(task_id=task_id, seed=seed, actions=actions, persona=persona)
    second = evaluate_trajectory(task_id=task_id, seed=seed, actions=actions, persona=persona)
    assert first.model_dump() == second.model_dump(), (
        f"non-deterministic grading for {task_id}/{seed}/{persona}"
    )
    return first


def assert_persona_invariant_headline(task_id: str, seed: int, actions: list[Action]) -> None:
    """INV-3: for fixed actions, ``score`` and ``breakdown`` are persona-invariant.

    ``total_reward`` is allowed to vary (personas only multiply per-step reward).
    """
    by_persona = {
        p: evaluate_trajectory(task_id=task_id, seed=seed, actions=actions, persona=p)
        for p in PERSONAS
    }
    base = by_persona[PERSONAS[0]]
    for persona, resp in by_persona.items():
        assert resp.score == base.score, (
            f"headline score drifted across personas ({persona} vs {PERSONAS[0]}) "
            f"for {task_id}/{seed}: {resp.score} != {base.score}"
        )
        assert resp.breakdown == base.breakdown, (
            f"breakdown drifted across personas ({persona}) for {task_id}/{seed}"
        )


# --- canonical deterministic rollout (golden snapshots) -------------------


def canonical_trace(
    task_id: str, seed: int, persona: str = "balanced", max_steps: int = 100
) -> list[Action]:
    """Deterministic BaselinePolicy rollout, used as the canonical golden trajectory.

    Mirrors ``benchmark/agents.py::BaselineAgent.run`` so the snapshot reflects a
    real, meaningful trajectory rather than an arbitrary action list. The trace is
    persona-independent (BaselinePolicy ignores persona), which is what makes the
    INV-3 persona-invariance check meaningful.
    """
    env = ExecutiveEmailEnv(task_id=task_id, seed=seed, persona=persona)
    observation = env.reset(task_id=task_id, seed=seed, persona=persona)
    policy = BaselinePolicy()
    trace: list[Action] = []
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
