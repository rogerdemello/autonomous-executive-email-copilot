from baseline.run_baseline import run


def test_baseline_is_deterministic_for_same_seed() -> None:
    first = run(task_id="hard_full_management", seed=42, max_steps=100, persona="balanced")
    second = run(task_id="hard_full_management", seed=42, max_steps=100, persona="balanced")

    assert first["score"] == second["score"]
    assert first["total_reward"] == second["total_reward"]
    assert first["steps"] == second["steps"]
    assert first["actions"] == second["actions"]
