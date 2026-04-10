"""Tests for LLM Agent - AI mode with mocked provider."""

import os
import unittest
from unittest.mock import MagicMock, patch

from env.llm_agent import LLMAgent
from env.models import Observation, ObservationEmail


def _make_observation() -> Observation:
    return Observation(
        emails=[
            ObservationEmail(
                id="e1",
                sender="client@company.com",
                sender_role="client",
                subject="URGENT: Contract issue",
                body="Need legal review before signing.",
                priority_hint="high",
                deadline_minutes=120,
                business_value=0.9,
                risk_tag="none",
                thread_history=[],
            ),
            ObservationEmail(
                id="e2",
                sender="vendor@supply.com",
                sender_role="vendor",
                subject="Invoice",
                body="Please process payment.",
                priority_hint="low",
                deadline_minutes=480,
                business_value=0.3,
                risk_tag="none",
                thread_history=[],
            ),
        ],
        time_remaining=180,
        pending_actions=["e1", "e2"],
        risk_level="medium",
        current_minute=10,
        persona="balanced",
        remaining_interruptions=1,
    )


class TestLLMAgentValidDecision(unittest.TestCase):
    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("env.llm_agent.OpenAI")
    def test_valid_decision_returns_success(self, mock_openai_class):
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content='{"action_type": "reply", "email_id": "e1", "content": "On it!"}'
                )
            )
        ]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        agent = LLMAgent()
        agent._did_prioritize = True

        obs = _make_observation()
        response = agent.get_action(obs)

        self.assertEqual(response.trace.status, "success")
        self.assertEqual(response.action.action_type, "reply")


class TestLLMAgentTimeout(unittest.TestCase):
    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("env.llm_agent.OpenAI")
    def test_timeout_returns_fallback_timeout(self, mock_openai_class):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = TimeoutError("Request timed out")
        mock_openai_class.return_value = mock_client

        agent = LLMAgent()
        agent._did_prioritize = True

        obs = _make_observation()
        response = agent.get_action(obs)

        self.assertEqual(response.trace.status, "fallback_timeout")
        self.assertEqual(response.action.action_type, "defer")


class TestLLMAgentMalformedJSON(unittest.TestCase):
    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("env.llm_agent.OpenAI")
    def test_malformed_json_returns_fallback_parse_error(self, mock_openai_class):
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content="not valid json {broken"))
        ]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        agent = LLMAgent()
        agent._did_prioritize = True

        obs = _make_observation()
        response = agent.get_action(obs)

        self.assertEqual(response.trace.status, "fallback_parse_error")


class TestLLMAgentUnsupportedAction(unittest.TestCase):
    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("env.llm_agent.OpenAI")
    def test_unsupported_action_returns_fallback_validation_error(self, mock_openai_class):
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content='{"action_type": "delete", "email_id": "e1"}'
                )
            )
        ]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        agent = LLMAgent()
        agent._did_prioritize = True

        obs = _make_observation()
        response = agent.get_action(obs)

        self.assertEqual(response.trace.status, "fallback_validation_error")


class TestLLMAgentProviderError(unittest.TestCase):
    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("env.llm_agent.OpenAI")
    def test_provider_error_returns_provider_error(self, mock_openai_class):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API rate limit exceeded")
        mock_openai_class.return_value = mock_client

        agent = LLMAgent()
        agent._did_prioritize = True

        obs = _make_observation()
        response = agent.get_action(obs)

        self.assertEqual(response.trace.status, "provider_error")


class TestLLMAgentGuardrails(unittest.TestCase):
    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("env.llm_agent.OpenAI")
    def test_first_action_prioritizes(self, mock_openai_class):
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        agent = LLMAgent()
        obs = _make_observation()

        response = agent.get_action(obs)

        self.assertEqual(response.action.action_type, "prioritize")
        self.assertEqual(response.trace.status, "success")
        self.assertEqual(len(response.action.priority_order), 2)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch("env.llm_agent.OpenAI")
    def test_legal_risk_auto_escalates(self, mock_openai_class):
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        agent = LLMAgent()
        agent._did_prioritize = True

        obs = Observation(
            emails=[
                ObservationEmail(
                    id="e1",
                    sender="legal@counsel.com",
                    sender_role="client",
                    subject="Contract review",
                    body="Legal document needs review.",
                    priority_hint="high",
                    deadline_minutes=60,
                    business_value=0.9,
                    risk_tag="legal",
                    thread_history=[],
                ),
            ],
            time_remaining=180,
            pending_actions=["e1"],
            risk_level="medium",
            current_minute=10,
            persona="balanced",
            remaining_interruptions=0,
        )

        response = agent.get_action(obs)

        self.assertEqual(response.action.action_type, "escalate")
        self.assertEqual(response.action.escalate_to, "legal_team")
        self.assertEqual(response.trace.status, "success")


if __name__ == "__main__":
    unittest.main()