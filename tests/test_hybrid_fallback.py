"""No-key hybrid fallback quality.

Without an LLM provider the hybrid planner/executor policy must NOT degenerate:
its deterministic fallback reuses the strong BaselinePolicy heuristics, so each
task scores well above 0 (competitive with the baseline) while never requiring a
key. These tests force the no-key path explicitly and grade the resulting
trajectory through the real grader.
"""

from __future__ import annotations

import pytest

from baseline.run_baseline import run
from env.environment import ExecutiveEmailEnv
from env.grader import evaluate_trajectory
from env.llm_policy import llm_provider_available
from env.policy import BaselinePolicy, HybridPolicy

TASKS = ["easy_classification", "medium_prioritization", "hard_full_management"]


@pytest.fixture
def no_key(monkeypatch):
    """Guarantee no provider key is visible (the .env is already isolated)."""
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert llm_provider_available() is False
    yield


def _run_hybrid(task_id: str, seed: int = 42, persona: str = "balanced"):
    env = ExecutiveEmailEnv(task_id=task_id, seed=seed, persona=persona)
    observation = env.reset(task_id=task_id, seed=seed, persona=persona)
    policy = HybridPolicy()
    trace = []
    for _ in range(100):
        action = policy.next_action(observation)
        if action is None:
            break
        trace.append(action)
        result = env.step(action)
        observation = result.observation
        if result.done:
            break
    graded = evaluate_trajectory(task_id=task_id, seed=seed, actions=trace, persona=persona)
    return trace, graded


@pytest.mark.parametrize("task_id", TASKS)
def test_hybrid_no_key_scores_well_above_zero(no_key, task_id):
    trace, graded = _run_hybrid(task_id)
    # Non-trivial trajectory: it actually acts on the inbox.
    assert len(trace) >= 3
    # Strong deterministic fallback: comfortably above a degenerate ~0 score.
    assert graded.score > 0.4, (task_id, graded.score)


@pytest.mark.parametrize("task_id", TASKS)
def test_hybrid_no_key_matches_baseline(no_key, task_id):
    """With no key the hybrid policy reuses BaselinePolicy, so it should match
    the baseline trajectory exactly rather than merely approximate it."""
    _, hybrid = _run_hybrid(task_id)
    baseline = run(task_id=task_id, seed=42, max_steps=100, persona="balanced", mode="baseline")
    assert hybrid.score == pytest.approx(baseline["score"], abs=1e-6), (
        task_id,
        hybrid.score,
        baseline["score"],
    )


def test_hybrid_no_key_uses_baseline_heuristics(no_key):
    """The fallback should be the real BaselinePolicy: same first action and a
    mix of classify/escalate/reply/defer actions (not a degenerate single op)."""
    trace, _ = _run_hybrid("hard_full_management")

    env = ExecutiveEmailEnv(task_id="hard_full_management", seed=42, persona="balanced")
    observation = env.reset(task_id="hard_full_management", seed=42, persona="balanced")
    baseline_first = BaselinePolicy().next_action(observation)

    assert trace[0].action_type == baseline_first.action_type == "prioritize"
    action_types = {a.action_type for a in trace}
    assert "classify" in action_types
    assert len(action_types) >= 3
