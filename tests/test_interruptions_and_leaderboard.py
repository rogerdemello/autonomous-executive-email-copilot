from baseline.leaderboard import build_leaderboard
from env.environment import ExecutiveEmailEnv
from env.models import Action


def test_all_tasks_include_interruptions() -> None:
    for task_id in ["easy_classification", "medium_prioritization", "hard_full_management"]:
        env = ExecutiveEmailEnv(task_id=task_id, seed=42, persona="balanced")
        observation = env.reset(task_id=task_id, seed=42, persona="balanced")
        before = len(observation.emails)

        order = [email.id for email in observation.emails]
        interruption_seen = False
        for _ in range(12):
            result = env.step(Action(action_type="prioritize", priority_order=order))
            if result.info.get("interruptions"):
                interruption_seen = True
                break

        after = len(env.state().emails)
        assert interruption_seen
        assert after > before


def test_leaderboard_shape_and_bounds() -> None:
    data = build_leaderboard(
        tasks=["easy_classification", "hard_full_management"],
        personas=["balanced", "strict_ceo"],
        seeds=[42, 43],
        max_steps=100,
    )

    rows = data["rows"]
    assert len(rows) == 4
    for row in rows:
        assert 0.0 <= row["avg_score"] <= 1.0
        assert 0.0 <= row["min_score"] <= 1.0
        assert 0.0 <= row["max_score"] <= 1.0


def test_stress_mode_persona_reward_gap() -> None:
    data = build_leaderboard(
        tasks=["hard_full_management"],
        personas=["strict_ceo", "chill_manager"],
        seeds=[42],
        max_steps=100,
        mode="stress",
        stress_rate=0.6,
    )

    strict_row = next(row for row in data["rows"] if row["persona"] == "strict_ceo")
    chill_row = next(row for row in data["rows"] if row["persona"] == "chill_manager")
    assert strict_row["avg_reward"] < chill_row["avg_reward"]


def test_leaderboard_csv_output(tmp_path) -> None:
    csv_path = tmp_path / "leaderboard.csv"
    data = build_leaderboard(
        tasks=["easy_classification"],
        personas=["balanced"],
        seeds=[42],
        max_steps=50,
        csv_out=str(csv_path),
    )

    assert data["csv_out"] == str(csv_path)
    assert csv_path.exists()
    text = csv_path.read_text(encoding="utf-8")
    assert "task,persona,avg_score,avg_reward,avg_steps,min_score,max_score" in text
