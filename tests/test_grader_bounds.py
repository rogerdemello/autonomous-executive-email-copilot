from env.grader import evaluate_trajectory
from env.models import Action


def test_grader_score_bounds_for_all_tasks_and_personas() -> None:
    tasks = [
        "easy_classification",
        "medium_prioritization",
        "hard_full_management",
    ]
    personas = ["strict_ceo", "balanced", "chill_manager"]

    for task in tasks:
        for persona in personas:
            result = evaluate_trajectory(task_id=task, seed=42, actions=[], persona=persona)
            assert 0.0 <= result.score <= 1.0
            for metric_score in result.breakdown.values():
                assert 0.0 <= metric_score <= 1.0


def test_strict_ceo_penalizes_mistakes_more_than_chill_manager() -> None:
    actions = [Action(action_type="classify") for _ in range(60)]

    strict = evaluate_trajectory(
        task_id="hard_full_management",
        seed=42,
        persona="strict_ceo",
        actions=actions,
    )
    chill = evaluate_trajectory(
        task_id="hard_full_management",
        seed=42,
        persona="chill_manager",
        actions=actions,
    )

    assert strict.total_reward < chill.total_reward
