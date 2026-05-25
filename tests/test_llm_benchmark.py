"""Benchmark tests for AI mode - verify AI works across persona variants and baseline remains deterministic."""

import os
import unittest
from unittest.mock import MagicMock, patch

from baseline.run_baseline import run


class TestBaselineDeterminism(unittest.TestCase):
    """Verify baseline deterministic behavior remains unchanged."""

    def test_baseline_deterministic_across_personas(self) -> None:
        """Baseline should produce identical results for same seed/persona."""
        personas = ["strict_ceo", "balanced", "chill_manager"]

        for persona in personas:
            first = run(
                task_id="hard_full_management",
                seed=42,
                max_steps=50,
                persona=persona,
                mode="baseline",
            )
            second = run(
                task_id="hard_full_management",
                seed=42,
                max_steps=50,
                persona=persona,
                mode="baseline",
            )

            self.assertEqual(first["score"], second["score"], f"Failed for persona: {persona}")
            self.assertEqual(first["total_reward"], second["total_reward"])
            self.assertEqual(first["steps"], second["steps"])
            self.assertEqual(first["actions"], second["actions"])

    def test_baseline_deterministic_across_tasks(self) -> None:
        """Baseline should produce identical results across different tasks."""
        tasks = ["easy_classification", "medium_prioritization", "hard_full_management"]

        for task in tasks:
            first = run(task_id=task, seed=42, max_steps=30, persona="balanced", mode="baseline")
            second = run(task_id=task, seed=42, max_steps=30, persona="balanced", mode="baseline")

            self.assertEqual(first["score"], second["score"], f"Failed for task: {task}")
            self.assertEqual(first["actions"], second["actions"])


class TestAIModePersonaVariants(unittest.TestCase):
    """Test AI mode works across persona variants with mocked LLM."""

    def _mock_llm_response(self, action_type: str = "reply", email_id: str = "e1"):
        """Helper to create mock LLM response."""
        import json

        content = json.dumps(
            {
                "action_type": action_type,
                "email_id": email_id,
                "content": "Working on it.",
                "priority_order": ["e1"],
                "escalate_to": None,
                "label": None,
            }
        )
        return content

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("env.llm_agent.OpenAI")
    def test_ai_mode_strict_ceo(self, mock_openai_class) -> None:
        """AI mode should work with strict_ceo persona."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content=self._mock_llm_response()))]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        result = run(
            task_id="hard_full_management",
            seed=42,
            max_steps=20,
            persona="strict_ceo",
            mode="llm",
        )

        self.assertIn("score", result)
        self.assertIn("total_reward", result)
        self.assertIn("breakdown", result)
        self.assertIn("actions", result)
        self.assertIn("decision_traces", result)
        self.assertEqual(result["persona"], "strict_ceo")
        self.assertEqual(len(result["decision_traces"]), result["steps"])

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("env.llm_agent.OpenAI")
    def test_ai_mode_balanced(self, mock_openai_class) -> None:
        """AI mode should work with balanced persona."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content=self._mock_llm_response()))]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        result = run(
            task_id="hard_full_management",
            seed=42,
            max_steps=20,
            persona="balanced",
            mode="llm",
        )

        self.assertIn("score", result)
        self.assertIn("total_reward", result)
        self.assertIn("breakdown", result)
        self.assertIn("actions", result)
        self.assertIn("decision_traces", result)
        self.assertEqual(result["persona"], "balanced")

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("env.llm_agent.OpenAI")
    def test_ai_mode_chill_manager(self, mock_openai_class) -> None:
        """AI mode should work with chill_manager persona."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content=self._mock_llm_response()))]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        result = run(
            task_id="hard_full_management",
            seed=42,
            max_steps=20,
            persona="chill_manager",
            mode="llm",
        )

        self.assertIn("score", result)
        self.assertIn("total_reward", result)
        self.assertIn("breakdown", result)
        self.assertIn("actions", result)
        self.assertIn("decision_traces", result)
        self.assertEqual(result["persona"], "chill_manager")


class TestAIAndBaselineOutputShape(unittest.TestCase):
    """Verify AI and baseline output shapes are comparable."""

    def _mock_llm_response(self, action_type: str = "reply", email_id: str = "e1"):
        """Helper to create mock LLM response."""
        import json

        content = json.dumps(
            {
                "action_type": action_type,
                "email_id": email_id,
                "content": "Done.",
                "priority_order": ["e1"],
                "escalate_to": None,
                "label": None,
            }
        )
        return content

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("env.llm_agent.OpenAI")
    def test_output_shape_comparable(self, mock_openai_class) -> None:
        """AI and baseline should have similar output structure."""
        # Mock LLM for AI mode
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content=self._mock_llm_response()))]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        baseline_result = run(
            task_id="easy_classification",
            seed=42,
            max_steps=10,
            persona="balanced",
            mode="baseline",
        )

        ai_result = run(
            task_id="easy_classification",
            seed=42,
            max_steps=10,
            persona="balanced",
            mode="llm",
        )

        # Both should have these common keys
        common_keys = {
            "task_id",
            "seed",
            "persona",
            "mode",
            "steps",
            "score",
            "total_reward",
            "breakdown",
            "actions",
        }

        for key in common_keys:
            self.assertIn(key, baseline_result, f"Baseline missing: {key}")
            self.assertIn(key, ai_result, f"AI missing: {key}")

        # AI should have decision_traces that baseline doesn't have
        self.assertIn("decision_traces", ai_result)
        self.assertNotIn("decision_traces", baseline_result)

        # Score should be in valid range [0, 1]
        self.assertGreaterEqual(baseline_result["score"], 0.0)
        self.assertLessEqual(baseline_result["score"], 1.0)
        self.assertGreaterEqual(ai_result["score"], 0.0)
        self.assertLessEqual(ai_result["score"], 1.0)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("env.llm_agent.OpenAI")
    def test_grader_bounds_for_ai_mode(self, mock_openai_class) -> None:
        """AI mode scores should be within valid bounds."""
        personas = ["strict_ceo", "balanced", "chill_manager"]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content=self._mock_llm_response()))]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        for persona in personas:
            result = run(
                task_id="hard_full_management",
                seed=42,
                max_steps=30,
                persona=persona,
                mode="llm",
            )

            self.assertGreaterEqual(result["score"], 0.0, f"Score below 0 for {persona}")
            self.assertLessEqual(result["score"], 1.0, f"Score above 1 for {persona}")

            for metric, value in result["breakdown"].items():
                self.assertGreaterEqual(value, 0.0, f"Metric {metric} below 0 for {persona}")
                self.assertLessEqual(value, 1.0, f"Metric {metric} above 1 for {persona}")


class TestRegressionAcrossPersonas(unittest.TestCase):
    """Regression tests - ensure AI mode doesn't regress across persona variants."""

    def _mock_llm_response(self, action_type: str = "reply", email_id: str = "e1"):
        """Helper to create mock LLM response."""
        import json

        content = json.dumps(
            {
                "action_type": action_type,
                "email_id": email_id,
                "content": "Done.",
                "priority_order": ["e1"],
                "escalate_to": None,
                "label": None,
            }
        )
        return content

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("env.llm_agent.OpenAI")
    def test_all_tasks_work_with_all_personas(self, mock_openai_class) -> None:
        """AI mode should work with all task/persona combinations."""
        tasks = ["easy_classification", "medium_prioritization", "hard_full_management"]
        personas = ["strict_ceo", "balanced", "chill_manager"]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content=self._mock_llm_response()))]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        for task in tasks:
            for persona in personas:
                result = run(
                    task_id=task,
                    seed=42,
                    max_steps=15,
                    persona=persona,
                    mode="llm",
                )

                self.assertIsNotNone(result, f"Failed for task={task}, persona={persona}")
                self.assertIn("score", result)
                self.assertIn("decision_traces", result)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("env.llm_agent.OpenAI")
    def test_decision_traces_populated(self, mock_openai_class) -> None:
        """Decision traces should be populated correctly."""
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content='{"action_type": "reply", "email_id": "e1", "content": "Done", "priority_order": ["e1"], "escalate_to": null, "label": null}'
                )
            )
        ]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        result = run(
            task_id="medium_prioritization",
            seed=42,
            max_steps=10,
            persona="balanced",
            mode="llm",
        )

        traces = result["decision_traces"]
        self.assertEqual(len(traces), result["steps"])

        # Each trace should have expected fields
        for trace in traces:
            self.assertIn("reason", trace)
            self.assertIn("status", trace)
            self.assertEqual(trace["status"], "success")


if __name__ == "__main__":
    unittest.main()
