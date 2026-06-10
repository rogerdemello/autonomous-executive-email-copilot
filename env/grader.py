"""Trajectory grader.

Scores are deliberately mapped into the **open** interval ``(0, 1)`` so they stay
numerically stable — downstream consumers never have to special-case exact
``0.0``/``1.0``:

- Task scores and breakdown metrics pass through ``strict_unit_interval`` which
  clips to ``[0, 1]`` then pulls the endpoints in by ``epsilon`` (1e-6).
- ``total_reward`` is unbounded (cumulative step reward), so it is squashed with
  ``0.5 + atan(value) / pi`` — a strictly increasing map onto ``(0, 1)`` that
  preserves ordering (better trajectories keep higher normalized reward) before
  the same open-interval clamp is applied.

These transforms are monotonic, so relative comparisons between trajectories are
preserved; only the absolute scale is bounded.
"""

from __future__ import annotations

import math

from .environment import ExecutiveEmailEnv
from .models import Action, GraderResponse, PersonaType, StepScoreBreakdown
from .safety.metric import compute_safety_metric
from .utils import strict_unit_interval


def _compute_score(metrics: dict[str, float], task_id: str) -> float:
    if task_id == "easy_classification":
        return metrics["classification_accuracy"]
    elif task_id == "medium_prioritization":
        return metrics["prioritization"]
    elif task_id == "hard_full_management":
        return (
            0.3 * metrics["classification_accuracy"]
            + 0.3 * metrics["action_correctness"]
            + 0.4 * metrics["response_quality"]
        )
    raise ValueError(f"Unknown task_id: {task_id}")


def _normalize_reward(value: float) -> float:
    # Map unbounded cumulative reward into (0,1) while preserving order.
    mapped = 0.5 + (math.atan(value) / math.pi)
    return strict_unit_interval(mapped)


def _generate_reason(action: Action, reward: float, info: dict[str, object]) -> str:
    action_type = action.action_type

    if action_type == "classify":
        correct = info.get("classification_correct")
        if correct is True:
            return f"Correct classification of email {action.email_id} as {action.label}"
        elif correct is False:
            return f"Wrong classification of email {action.email_id}: expected something else"
        return f"Classification of email {action.email_id}"

    elif action_type == "prioritize":
        similarity = info.get("ranking_similarity", 0)
        return f"Prioritization action, similarity: {similarity:.2f}"

    elif action_type == "reply":
        quality = info.get("reply_quality", 0)
        if reward > 0:
            return f"Replied to email {action.email_id}, quality: {quality:.2f}"
        else:
            return f"Reply to email {action.email_id} was incorrect or inappropriate"

    elif action_type == "escalate":
        return f"Escalated email {action.email_id}"

    elif action_type == "defer":
        return f"Deferred email {action.email_id}"

    return f"Action: {action_type}"


def evaluate_trajectory(
    task_id: str,
    seed: int,
    actions: list[Action],
    persona: PersonaType = "balanced",
) -> GraderResponse:
    env = ExecutiveEmailEnv(task_id=task_id, seed=seed, persona=persona)
    done = False
    step_breakdown: list[StepScoreBreakdown] = []
    score_before = 0.0

    for idx, action in enumerate(actions):
        if done:
            break

        metrics_before = env.metrics()
        score_before = _compute_score(metrics_before, task_id)

        result = env.step(action)
        done = result.done

        metrics_after = env.metrics()
        score_after = _compute_score(metrics_after, task_id)

        delta = score_after - score_before

        reason = _generate_reason(action, result.reward, result.info)

        step_breakdown.append(
            StepScoreBreakdown(
                step_number=idx + 1,
                action=action.action_type,
                email_id=action.email_id,
                score_delta=round(delta, 6),
                reason=reason,
            )
        )

    metrics = env.metrics()
    total_reward = env.state().total_reward
    final_score = _compute_score(metrics, task_id)

    if task_id == "easy_classification":
        breakdown = {
            "classification_accuracy": metrics["classification_accuracy"],
        }
    elif task_id == "medium_prioritization":
        breakdown = {
            "prioritization": metrics["prioritization"],
            "resolved_ratio": metrics["resolved_ratio"],
        }
    elif task_id == "hard_full_management":
        breakdown = {
            "classification_accuracy": metrics["classification_accuracy"],
            "action_correctness": metrics["action_correctness"],
            "response_quality": metrics["response_quality"],
            "prioritization": metrics["prioritization"],
            "resolved_ratio": metrics["resolved_ratio"],
        }
    else:
        raise ValueError(f"Unknown task_id: {task_id}")

    strict_score = strict_unit_interval(final_score)
    strict_breakdown = {k: strict_unit_interval(v) for k, v in breakdown.items()}
    strict_total_reward = _normalize_reward(total_reward)

    # Out-of-band safety metric: computed from the actions + final email state,
    # bounded into (0, 1). Reported alongside the headline; never mixed into score.
    safety_score = compute_safety_metric(actions, env.state().emails)

    return GraderResponse(
        task_id=task_id,
        seed=seed,
        persona=persona,
        score=round(strict_score, 6),
        breakdown={k: round(v, 6) for k, v in strict_breakdown.items()},
        total_reward=round(strict_total_reward, 6),
        safety_score=round(safety_score, 6),
        step_breakdown=step_breakdown,
    )
