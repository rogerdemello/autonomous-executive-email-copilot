from __future__ import annotations

from .environment import ExecutiveEmailEnv
from .models import Action, GraderResponse, PersonaType
from .utils import clip_score


SCORE_EPSILON = 1e-6


def evaluate_trajectory(
    task_id: str,
    seed: int,
    actions: list[Action],
    persona: PersonaType = "balanced",
) -> GraderResponse:
    env = ExecutiveEmailEnv(task_id=task_id, seed=seed, persona=persona)
    done = False

    for action in actions:
        if done:
            break
        result = env.step(action)
        done = result.done

    metrics = env.metrics()
    total_reward = env.state().total_reward

    if task_id == "easy_classification":
        score = metrics["classification_accuracy"]
        breakdown = {
            "classification_accuracy": metrics["classification_accuracy"],
        }
    elif task_id == "medium_prioritization":
        score = metrics["prioritization"]
        breakdown = {
            "prioritization": metrics["prioritization"],
            "resolved_ratio": metrics["resolved_ratio"],
        }
    elif task_id == "hard_full_management":
        score = (
            0.3 * metrics["classification_accuracy"]
            + 0.3 * metrics["action_correctness"]
            + 0.4 * metrics["response_quality"]
        )
        breakdown = {
            "classification_accuracy": metrics["classification_accuracy"],
            "action_correctness": metrics["action_correctness"],
            "response_quality": metrics["response_quality"],
            "prioritization": metrics["prioritization"],
            "resolved_ratio": metrics["resolved_ratio"],
        }
    else:
        raise ValueError(f"Unknown task_id: {task_id}")

    bounded_score = clip_score(score)
    strict_score = min(1.0 - SCORE_EPSILON, max(SCORE_EPSILON, bounded_score))

    return GraderResponse(
        task_id=task_id,
        seed=seed,
        persona=persona,
        score=round(strict_score, 6),
        breakdown={k: round(v, 6) for k, v in breakdown.items()},
        total_reward=round(total_reward, 6),
    )
