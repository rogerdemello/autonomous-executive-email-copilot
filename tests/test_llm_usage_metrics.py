"""LLM observability is wired into the live success path.

The successful ``LLMAgent.get_action`` path must report the real latency,
cost, token counts, and model to ``telemetry.metrics.record_llm_usage`` -- and
must never let a telemetry failure break the agent.
"""

import os
import unittest
from unittest.mock import MagicMock, patch

import pytest

from env.llm_agent import LLMAgent
from env.models import Observation, ObservationEmail


@pytest.fixture(autouse=True)
def _isolate_llm_cache():
    """Clear the module-global response cache around every test so a cached
    response can never short-circuit the live (API-calling) path under test."""
    from env.llm_agent import _response_cache

    _response_cache.clear()
    yield
    _response_cache.clear()


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
        ],
        time_remaining=180,
        pending_actions=["e1"],
        risk_level="medium",
        current_minute=10,
        persona="balanced",
        remaining_interruptions=1,
    )


def _mock_client(mock_openai_class, *, prompt_tokens=100, completion_tokens=40):
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content='{"action_type": "reply", "email_id": "e1", "content": "On it!"}'
            )
        )
    ]
    mock_response.usage = MagicMock(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_response
    mock_openai_class.return_value = mock_client
    return mock_client


class TestLLMUsageMetrics(unittest.TestCase):
    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False)
    @patch("telemetry.metrics.record_llm_usage")
    @patch("env.llm_agent.OpenAI")
    def test_records_usage_once_on_success(self, mock_openai_class, mock_record):
        _mock_client(mock_openai_class, prompt_tokens=100, completion_tokens=40)

        agent = LLMAgent(require_approval=False)
        agent._did_prioritize = True

        response = agent.get_action(_make_observation())

        self.assertEqual(response.trace.status, "success")
        mock_record.assert_called_once()

        _, kwargs = mock_record.call_args
        self.assertEqual(kwargs["prompt_tokens"], 100)
        self.assertEqual(kwargs["completion_tokens"], 40)
        self.assertEqual(kwargs["cost_usd"], response.trace.cost_usd)
        self.assertEqual(kwargs["model"], response.trace.model_name)
        self.assertEqual(kwargs["latency_ms"], response.trace.latency_ms)
        self.assertIsInstance(kwargs["latency_ms"], int)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False)
    @patch("telemetry.metrics.record_llm_usage")
    @patch("env.llm_agent.OpenAI")
    def test_telemetry_failure_does_not_break_agent(self, mock_openai_class, mock_record):
        _mock_client(mock_openai_class)
        mock_record.side_effect = RuntimeError("metrics backend down")

        agent = LLMAgent(require_approval=False)
        agent._did_prioritize = True

        # A telemetry explosion must not propagate: the agent still answers.
        response = agent.get_action(_make_observation())

        self.assertEqual(response.trace.status, "success")
        mock_record.assert_called_once()

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False)
    @patch("telemetry.metrics.record_llm_usage")
    @patch("env.llm_agent.OpenAI")
    def test_cache_hit_does_not_record(self, mock_openai_class, mock_record):
        _mock_client(mock_openai_class)

        agent = LLMAgent(require_approval=False)
        agent._did_prioritize = True

        obs = _make_observation()
        first = agent.get_action(obs)
        self.assertEqual(first.trace.status, "success")
        self.assertEqual(mock_record.call_count, 1)

        # Second identical observation is served from cache -> no API call,
        # therefore no second usage record. Clear per-email progress so the same
        # observation is re-presented (handled emails are otherwise hidden).
        agent._handled_ids.clear()
        second = agent.get_action(obs)
        self.assertEqual(mock_record.call_count, 1, "cache hit must not record LLM usage")
        self.assertEqual(second.action.action_type, first.action.action_type)


if __name__ == "__main__":
    unittest.main()
