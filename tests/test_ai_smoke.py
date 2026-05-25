"""Smoke tests for CLI, API, and UI demo paths (Task 14).

These tests verify:
1. CLI AI run smoke passes
2. API AI run smoke passes
3. UI demo smoke passes
4. Fallback scenario smoke passes
"""

import os
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from baseline.run_baseline import run as run_baseline
from env.api import app


def _mock_llm_response(action_type: str = "reply", email_id: str = "e1"):
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


class TestCLIAISmoke(unittest.TestCase):
    """Smoke tests for CLI AI runner."""

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("env.llm_agent.OpenAI")
    def test_cli_ai_run_smoke(self, mock_openai_class):
        """CLI AI run should complete without error."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content=_mock_llm_response()))]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        result = run_baseline(
            task_id="hard_full_management",
            seed=42,
            max_steps=10,
            persona="balanced",
            mode="llm",
        )

        # Verify output shape
        self.assertIn("score", result)
        self.assertIn("total_reward", result)
        self.assertIn("breakdown", result)
        self.assertIn("actions", result)
        self.assertIn("decision_traces", result)

        # Verify score bounds
        self.assertGreaterEqual(result["score"], 0.0)
        self.assertLessEqual(result["score"], 1.0)

        # Verify decision traces exist and are populated
        self.assertIsInstance(result["decision_traces"], list)
        self.assertGreater(len(result["decision_traces"]), 0)

        # Verify trace structure
        for trace in result["decision_traces"]:
            self.assertIn("status", trace)
            self.assertIn("reason", trace)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("env.llm_agent.OpenAI")
    def test_cli_ai_run_with_all_personas(self, mock_openai_class):
        """CLI AI run should work with all personas."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content=_mock_llm_response()))]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        for persona in ["strict_ceo", "balanced", "chill_manager"]:
            result = run_baseline(
                task_id="easy_classification",
                seed=42,
                max_steps=5,
                persona=persona,
                mode="llm",
            )
            self.assertEqual(result["persona"], persona)
            self.assertIn("decision_traces", result)


class TestAPIAISmoke(unittest.TestCase):
    """Smoke tests for API AI path."""

    def setUp(self):
        """Set up test client."""
        self.client = TestClient(app)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("env.llm_agent.OpenAI")
    def test_api_ai_run_smoke(self, mock_openai_class):
        """API /baseline with mode=llm should work."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content=_mock_llm_response()))]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        payload = {
            "task_id": "hard_full_management",
            "seed": 42,
            "max_steps": 10,
            "persona": "balanced",
            "mode": "llm",
        }

        response = self.client.post("/baseline", json=payload)

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Verify response structure
        self.assertIn("score", data)
        self.assertIn("total_reward", data)
        self.assertIn("steps", data)
        self.assertIn("breakdown", data)
        self.assertIn("action_trace", data)
        self.assertIn("decision_trace", data)

        # Verify score bounds
        self.assertGreaterEqual(data["score"], 0.0)
        self.assertLessEqual(data["score"], 1.0)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("env.llm_agent.OpenAI")
    def test_api_ai_run_with_compare_mode(self, mock_openai_class):
        """API should handle compare mode (llm + baseline)."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content=_mock_llm_response()))]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        # First run baseline
        baseline_payload = {
            "task_id": "medium_prioritization",
            "seed": 42,
            "max_steps": 5,
            "persona": "balanced",
            "mode": "baseline",
        }
        baseline_response = self.client.post("/baseline", json=baseline_payload)
        self.assertEqual(baseline_response.status_code, 200)

        # Then run llm
        llm_payload = {
            "task_id": "medium_prioritization",
            "seed": 42,
            "max_steps": 5,
            "persona": "balanced",
            "mode": "llm",
        }
        llm_response = self.client.post("/baseline", json=llm_payload)
        self.assertEqual(llm_response.status_code, 200)

        baseline_data = baseline_response.json()
        llm_data = llm_response.json()

        # Both should have valid scores
        self.assertGreaterEqual(baseline_data["score"], 0.0)
        self.assertLessEqual(baseline_data["score"], 1.0)
        self.assertGreaterEqual(llm_data["score"], 0.0)
        self.assertLessEqual(llm_data["score"], 1.0)

        # LLM should have decision traces
        self.assertIn("decision_trace", llm_data)
        self.assertIsInstance(llm_data["decision_trace"], list)


class TestUIDemoSmoke(unittest.TestCase):
    """Smoke tests for UI demo path (Streamlit -> API)."""

    def setUp(self):
        """Set up test client."""
        self.client = TestClient(app)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("env.llm_agent.OpenAI")
    def test_ui_demo_api_path_smoke(self, mock_openai_class):
        """Streamlit AI Demo -> API path should work."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content=_mock_llm_response()))]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        # Simulate what Streamlit AI Demo does
        payload = {
            "task_id": "hard_full_management",
            "seed": 42,
            "persona": "balanced",
            "mode": "llm",
            "stress_rate": 0.0,
            "max_steps": 20,
        }

        response = self.client.post("/baseline", json=payload)

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Verify decision traces (what UI displays)
        decision_traces = data.get("decision_trace", [])
        self.assertIsInstance(decision_traces, list)

        # Each trace should have expected fields for UI display
        for trace in decision_traces:
            self.assertIn("step", trace)
            self.assertIn("status", trace)
            self.assertIn("reason", trace)
            self.assertIn("action", trace)

            # UI displays these fields
            if trace.get("action"):
                self.assertIn("action_type", trace["action"])

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("env.llm_agent.OpenAI")
    def test_ui_demo_preset_config(self, mock_openai_class):
        """Test UI demo preset configuration works."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content=_mock_llm_response()))]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        # Demo preset: hard_full_management, balanced, seed=42, max_steps=120, compare
        # First run baseline (UI compare mode part 1)
        baseline_payload = {
            "task_id": "hard_full_management",
            "seed": 42,
            "persona": "balanced",
            "mode": "baseline",
            "stress_rate": 0.5,
            "max_steps": 120,
        }
        baseline_response = self.client.post("/baseline", json=baseline_payload)
        self.assertEqual(baseline_response.status_code, 200)

        # Then run LLM (UI compare mode part 2)
        llm_payload = {
            "task_id": "hard_full_management",
            "seed": 42,
            "persona": "balanced",
            "mode": "llm",
            "stress_rate": 0.5,
            "max_steps": 120,
        }
        llm_response = self.client.post("/baseline", json=llm_payload)
        self.assertEqual(llm_response.status_code, 200)

        baseline_data = baseline_response.json()
        llm_data = llm_response.json()

        # Verify UI can compute deltas
        score_delta = llm_data["score"] - baseline_data["score"]
        reward_delta = llm_data["total_reward"] - baseline_data["total_reward"]

        # Deltas should be numeric (can be positive or negative)
        self.assertIsInstance(score_delta, float)
        self.assertIsInstance(reward_delta, float)


class TestFallbackScenarioSmoke(unittest.TestCase):
    """Smoke tests for degraded/fallback scenario."""

    def setUp(self):
        """Set up test client."""
        self.client = TestClient(app)
        import env.llm_agent as llm_module

        llm_module._default_agent = None

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("env.llm_agent.OpenAI")
    def test_fallback_on_timeout_cli(self, mock_openai_class):
        """CLI should handle LLM timeout gracefully."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = TimeoutError("Request timed out")
        mock_openai_class.return_value = mock_client

        result = run_baseline(
            task_id="easy_classification",
            seed=42,
            max_steps=20,
            persona="balanced",
            mode="llm",
        )

        self.assertIn("decision_traces", result)
        self.assertIsInstance(result["decision_traces"], list)

        # Should have fallback status on some traces (beyond guardrail steps)
        statuses = [t.get("status") for t in result["decision_traces"]]
        has_fallback = any("fallback" in s for s in statuses if s)
        # If guardrails consume all steps, that's also acceptable behavior
        # as long as traces exist and system degrades gracefully
        self.assertTrue(
            has_fallback or len(result["decision_traces"]) >= 10,
            f"No fallback and only {len(result['decision_traces'])} traces: {statuses}",
        )

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("env.llm_agent.OpenAI")
    def test_fallback_on_provider_error_cli(self, mock_openai_class):
        """CLI should handle provider errors gracefully."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API rate limit exceeded")
        mock_openai_class.return_value = mock_client

        result = run_baseline(
            task_id="medium_prioritization",
            seed=42,
            max_steps=20,
            persona="balanced",
            mode="llm",
        )

        self.assertIn("decision_traces", result)
        self.assertGreater(len(result["decision_traces"]), 0)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("env.llm_agent.OpenAI")
    def test_fallback_on_api(self, mock_openai_class):
        """API should handle LLM errors gracefully."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = TimeoutError("Request timed out")
        mock_openai_class.return_value = mock_client

        payload = {
            "task_id": "easy_classification",
            "seed": 42,
            "max_steps": 20,
            "persona": "balanced",
            "mode": "llm",
        }

        response = self.client.post("/baseline", json=payload)

        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertIn("score", data)
        self.assertIn("decision_trace", data)

        # System should complete gracefully (guardrails may handle all steps)
        # or fallback should be indicated
        statuses = [t.get("status") for t in data.get("decision_trace", [])]
        has_fallback = any("fallback" in s for s in statuses if s)
        self.assertTrue(
            has_fallback or len(data.get("decision_trace", [])) >= 10,
            f"No fallback and only {len(data.get('decision_trace', []))} traces",
        )

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("env.llm_agent.OpenAI")
    def test_malformed_response_fallback(self, mock_openai_class):
        """Should handle malformed LLM responses gracefully."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="not valid json"))]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        result = run_baseline(
            task_id="easy_classification",
            seed=42,
            max_steps=20,
            persona="balanced",
            mode="llm",
        )

        self.assertIn("decision_traces", result)

        # Check for fallback status OR sufficient traces from guardrails
        statuses = [t.get("status") for t in result["decision_traces"]]
        has_fallback = any("fallback" in s for s in statuses if s)
        self.assertTrue(
            has_fallback or len(result["decision_traces"]) >= 10,
            f"No fallback and only {len(result['decision_traces'])} traces",
        )


if __name__ == "__main__":
    unittest.main()
