"""Score and log-format contract tests.

These guard two stable contracts the rest of the stack relies on:
- Scores are in the open interval (0.0, 1.0) - NOT inclusive - so downstream
  consumers never have to special-case exact 0.0/1.0.
- The `inference.py` CLI log format is exact: [START], [STEP], [END] lines
  remain machine-parseable for tooling and reproducibility.
"""

import re

from env.grader import evaluate_trajectory
from env.models import Action

TASKS = ["easy_classification", "medium_prioritization", "hard_full_management"]


class TestScoreOpenInterval:
    """Test that scores are in open interval (0.0, 1.0) - NOT inclusive."""

    def test_score_not_zero_or_one_for_all_tasks_empty_actions(self) -> None:
        """Verify scores are strictly in (0.0, 1.0) for all tasks with no actions."""
        for task in TASKS:
            result = evaluate_trajectory(task_id=task, seed=42, actions=[], persona="balanced")
            assert result.score > 0.0, f"Score must be > 0.0 for {task}, got {result.score}"
            assert result.score < 1.0, f"Score must be < 1.0 for {task}, got {result.score}"

    def test_score_not_zero_or_one_for_all_tasks_with_actions(self) -> None:
        """Verify scores are strictly in (0.0, 1.0) for all tasks with some actions."""
        for task in TASKS:
            actions = [
                Action(action_type="classify", email_id="e1", label="normal") for _ in range(5)
            ]
            result = evaluate_trajectory(task_id=task, seed=42, actions=actions, persona="balanced")
            assert result.score > 0.0, f"Score must be > 0.0 for {task}, got {result.score}"
            assert result.score < 1.0, f"Score must be < 1.0 for {task}, got {result.score}"

    def test_breakdown_scores_not_zero_or_one(self) -> None:
        """Verify all breakdown metric scores are in open interval (0.0, 1.0)."""
        for task in TASKS:
            result = evaluate_trajectory(task_id=task, seed=42, actions=[], persona="balanced")
            for metric_name, metric_score in result.breakdown.items():
                assert metric_score > 0.0, f"{metric_name} must be > 0.0, got {metric_score}"
                assert metric_score < 1.0, f"{metric_name} must be < 1.0, got {metric_score}"


class TestInferenceLogFormat:
    """Test inference log format parsing."""

    def test_start_format(self) -> None:
        """Test [START] format: task=X env=Y model=Z"""
        log_line = "[START] task=easy_classification env=local model=gpt-4o-mini"

        pattern = r"^\[START\] task=(\w+) env=(\w+) model=([\w\-]+)$"
        match = re.match(pattern, log_line)
        assert match is not None, f"Invalid START format: {log_line}"

        task, env, model = match.groups()
        assert task == "easy_classification"
        assert env == "local"
        assert model == "gpt-4o-mini"

    def test_step_format(self) -> None:
        """Test [STEP] format: step=N action=A reward=R done=D error=E"""
        log_line = "[STEP] step=0 action=classify:e1 reward=0.2 done=False error=none"

        pattern = r"^\[STEP\] step=(\d+) action=(\S+) reward=([\-\d.]+) done=(\w+) error=(\S+)$"
        match = re.match(pattern, log_line)
        assert match is not None, f"Invalid STEP format: {log_line}"

        step, action, reward, done, error = match.groups()
        assert step == "0"
        assert action == "classify:e1"
        assert float(reward) == 0.2
        assert done == "False"
        assert error == "none"

    def test_end_format(self) -> None:
        """Test [END] format: success=S steps=T score=Sc rewards=Rl"""
        log_line = "[END] success=True steps=10 score=0.750000 rewards=2.3456"

        pattern = r"^\[END\] success=(\w+) steps=(\d+) score=([\d.]+) rewards=([\-\d.]+)$"
        match = re.match(pattern, log_line)
        assert match is not None, f"Invalid END format: {log_line}"

        success, steps, score, rewards = match.groups()
        assert success == "True"
        assert steps == "10"
        assert float(score) == 0.750000
        assert float(rewards) == 2.3456

    def test_start_format_task_env_model_variations(self) -> None:
        """Test START format with different tasks and models."""
        test_cases = [
            "[START] task=easy_classification env=local model=gpt-4o",
            "[START] task=medium_prioritization env=azure model=gpt-4",
            "[START] task=hard_full_management env=test model=claude-3",
        ]

        pattern = r"^\[START\] task=(\w+) env=(\w+) model=([\w\-]+)$"
        for log_line in test_cases:
            match = re.match(pattern, log_line)
            assert match is not None, f"Invalid START format: {log_line}"

    def test_step_format_with_error(self) -> None:
        """Test STEP format with non-empty error."""
        log_line = "[STEP] step=5 action=reply:e2 reward=-0.5 done=True error=API_timeout"

        pattern = r"^\[STEP\] step=(\d+) action=(\S+) reward=([\-\d.]+) done=(\w+) error=(\S+)$"
        match = re.match(pattern, log_line)
        assert match is not None, f"Invalid STEP format: {log_line}"

        step, action, reward, done, error = match.groups()
        assert step == "5"
        assert action == "reply:e2"
        assert float(reward) == -0.5
        assert done == "True"
        assert error == "API_timeout"

    def test_end_format_score_bounds(self) -> None:
        """Test END format score is in open interval."""
        log_line = "[END] success=True steps=15 score=0.999999 rewards=5.0000"

        pattern = r"^\[END\] success=(\w+) steps=(\d+) score=([\d.]+) rewards=([\-\d.]+)$"
        match = re.match(pattern, log_line)
        assert match is not None

        score = float(match.group(3))
        assert score > 0.0, f"Score must be > 0.0, got {score}"
        assert score < 1.0, f"Score must be < 1.0, got {score}"


class TestLogParsingIntegration:
    """Test full log parsing from inference output."""

    def test_parse_full_inference_log(self) -> None:
        """Test parsing a full inference log sequence."""
        logs = [
            "[START] task=easy_classification env=local model=gpt-4o-mini",
            "[STEP] step=0 action=classify:e1 reward=0.2 done=False error=none",
            "[STEP] step=1 action=classify:e2 reward=0.2 done=False error=none",
            "[END] success=True steps=2 score=0.850000 rewards=0.4000",
        ]

        # Parse START
        start_match = re.match(r"^\[START\] task=(\w+) env=(\w+) model=(\S+)$", logs[0])
        assert start_match is not None
        assert start_match.group(1) == "easy_classification"

        # Parse STEPs
        step_pattern = (
            r"^\[STEP\] step=(\d+) action=(\S+) reward=([\-\d.]+) done=(\w+) error=(\S+)$"
        )
        for log in logs[1:3]:
            step_match = re.match(step_pattern, log)
            assert step_match is not None

        # Parse END
        end_match = re.match(
            r"^\[END\] success=(\w+) steps=(\d+) score=([\d.]+) rewards=([\-\d.]+)$", logs[3]
        )
        assert end_match is not None
        assert end_match.group(1) == "True"
        assert int(end_match.group(2)) == 2
        score = float(end_match.group(3))
        assert 0.0 < score < 1.0


class TestAllTasksValidated:
    """Test all 3 tasks with score contract."""

    def test_easy_classification_score_contract(self) -> None:
        """Test easy_classification produces valid scores."""
        result = evaluate_trajectory(
            task_id="easy_classification",
            seed=42,
            actions=[],
            persona="balanced",
        )
        assert 0.0 < result.score < 1.0
        assert result.score == round(result.score, 6)

    def test_medium_prioritization_score_contract(self) -> None:
        """Test medium_prioritization produces valid scores."""
        result = evaluate_trajectory(
            task_id="medium_prioritization",
            seed=42,
            actions=[],
            persona="balanced",
        )
        assert 0.0 < result.score < 1.0
        assert result.score == round(result.score, 6)

    def test_hard_full_management_score_contract(self) -> None:
        """Test hard_full_management produces valid scores."""
        result = evaluate_trajectory(
            task_id="hard_full_management",
            seed=42,
            actions=[],
            persona="balanced",
        )
        assert 0.0 < result.score < 1.0
        assert result.score == round(result.score, 6)

    def test_all_tasks_with_actions_score_contract(self) -> None:
        """Test all tasks with actions produce valid scores."""
        for task in TASKS:
            actions = [
                Action(action_type="classify", email_id="e1", label="normal") for _ in range(3)
            ]
            result = evaluate_trajectory(task_id=task, seed=42, actions=actions, persona="balanced")
            assert 0.0 < result.score < 1.0, f"Score {result.score} out of bounds for {task}"

    def test_all_personas_score_contract(self) -> None:
        """Test all personas produce valid scores."""
        personas = ["strict_ceo", "balanced", "chill_manager"]
        for persona in personas:
            result = evaluate_trajectory(
                task_id="hard_full_management",
                seed=42,
                actions=[],
                persona=persona,
            )
            assert 0.0 < result.score < 1.0, f"Score {result.score} out of bounds for {persona}"
