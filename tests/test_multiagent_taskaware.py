from __future__ import annotations

from benchmark.agents import MultiAgent
from env.agents.coordinator import CoordinatorAgent
from env.environment import ExecutiveEmailEnv


def _first_action(task_id: str, seed: int = 42, persona: str = "balanced"):
    """Reset the env for a task and ask a task-aware coordinator for its first move."""
    env = ExecutiveEmailEnv(task_id=task_id, seed=seed, persona=persona)
    observation = env.reset(task_id=task_id, seed=seed, persona=persona)
    coordinator = CoordinatorAgent(task_id=task_id)
    return coordinator.execute(observation)


def test_coordinator_priority_order_is_task_aware():
    # Classification/prioritization bias toward the ClassifierAgent; the hard task
    # keeps the original risk-first escalation ordering.
    assert CoordinatorAgent(task_id="easy_classification")._get_priority_order()[0] == (
        "ClassifierAgent"
    )
    assert CoordinatorAgent(task_id="medium_prioritization")._get_priority_order()[0] == (
        "ClassifierAgent"
    )
    assert CoordinatorAgent(task_id="hard_full_management")._get_priority_order()[0] == (
        "EscalatorAgent"
    )


def test_coordinator_defaults_to_hard_behavior():
    # Default construction (no task supplied) preserves the legacy risk-first order,
    # so existing callers keep their behavior.
    assert CoordinatorAgent()._get_priority_order()[0] == "EscalatorAgent"


def test_set_task_updates_priority_order():
    coordinator = CoordinatorAgent()
    assert coordinator._get_priority_order()[0] == "EscalatorAgent"
    coordinator.set_task("easy_classification")
    assert coordinator._get_priority_order()[0] == "ClassifierAgent"


def test_easy_classification_emits_classify_action():
    # The inbox carries risk-tagged emails, so EscalatorAgent.can_handle is True; a
    # task-blind coordinator would escalate. The task-aware coordinator must classify.
    action = _first_action("easy_classification")
    assert action is not None
    assert action.action_type == "classify"


def test_easy_classification_scores_well_above_zero():
    metrics = MultiAgent().run(task_id="easy_classification", seed=42, persona="balanced")
    # Task-blind coordinator floored at ~0 (epsilon) by escalating instead of
    # classifying; task-aware routing must beat that decisively.
    assert metrics.score > 0.5


def test_medium_prioritization_emits_prioritize_first():
    # None of the content specialists emit a ranking, so the task-aware coordinator
    # must open the prioritization task with a `prioritize` action.
    action = _first_action("medium_prioritization")
    assert action is not None
    assert action.action_type == "prioritize"
    assert action.priority_order  # non-empty ranking


def test_medium_prioritization_scores_well_above_zero():
    metrics = MultiAgent().run(task_id="medium_prioritization", seed=42, persona="balanced")
    assert metrics.score > 0.5


def test_hard_full_management_still_escalates_risk():
    # Genuine legal/security risk must still be escalated on the hard task.
    action = _first_action("hard_full_management")
    assert action is not None
    assert action.action_type == "escalate"
