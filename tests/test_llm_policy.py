from __future__ import annotations

from unittest.mock import patch

from app.core.models import Observation, ObservationEmail
from app.llm.policy import (
    LLMPolicy,
    Planner,
    Strategy,
    _parse_strategy_response,
    _validate_strategy,
    get_strategy,
    llm_provider_available,
    reset_planner,
)


class TestLLMProviderAvailable:
    def test_returns_true_when_hf_token_set(self, monkeypatch) -> None:
        monkeypatch.setenv("HF_TOKEN", "hf_test_key")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        # Re-create settings so the new env is picked up
        assert llm_provider_available() is True

    def test_returns_true_when_openai_key_set(self, monkeypatch) -> None:
        monkeypatch.delenv("HF_TOKEN", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
        assert llm_provider_available() is True

    def test_returns_false_when_no_key_set(self, monkeypatch) -> None:
        monkeypatch.delenv("HF_TOKEN", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
        assert llm_provider_available() is False


class TestStrategyEnum:
    def test_has_all_expected_values(self) -> None:
        expected = {
            "PRIORITIZE_URGENT": "prioritize_urgent",
            "BATCH_REPLY": "batch_reply",
            "ESCALATE_CRITICAL": "escalate_critical",
            "DEFER_LOW_VALUE": "defer_low_value",
            "MONITOR": "monitor",
        }
        for name, value in expected.items():
            assert getattr(Strategy, name).value == value

    def test_values_are_unique(self) -> None:
        values = [s.value for s in Strategy]
        assert len(values) == len(set(values))


class TestParseStrategyResponse:
    def test_with_valid_json(self) -> None:
        raw = '{"strategy": "prioritize_urgent", "reasoning": "test", "confidence": 0.8}'
        result = _parse_strategy_response(raw)
        assert result is not None
        assert result["strategy"] == "prioritize_urgent"
        assert result["confidence"] == 0.8

    def test_with_json_in_markdown_code_block(self) -> None:
        text = 'Some text\n```json\n{"strategy": "batch_reply", "reasoning": "ok", "confidence": 0.5}\n```'
        result = _parse_strategy_response(text)
        assert result is not None
        assert result["strategy"] == "batch_reply"

    def test_with_invalid_input_returns_none(self) -> None:
        assert _parse_strategy_response("not json at all") is None
        assert _parse_strategy_response("") is None
        assert _parse_strategy_response("```json\nnot valid json\n```") is None


class TestValidateStrategy:
    def test_with_valid_strategy_dict(self) -> None:
        result = _validate_strategy({"strategy": "monitor"})
        assert result == Strategy.MONITOR

    def test_with_invalid_strategy_name_returns_none(self) -> None:
        result = _validate_strategy({"strategy": "fly_to_moon"})
        assert result is None

    def test_with_missing_strategy_key_returns_none(self) -> None:
        result = _validate_strategy({"reasoning": "no strategy here"})
        assert result is None


class TestPlanner:
    def test_fallback_strategy(self) -> None:
        planner = Planner()
        strategy, metadata = planner._fallback_strategy()
        assert strategy == Strategy.PRIORITIZE_URGENT
        assert metadata["confidence"] == 0.3

    def test_plan_with_critical_risk_email_returns_escalate_critical(self) -> None:
        obs = Observation(
            emails=[
                ObservationEmail(
                    id="e1",
                    sender="attacker@bad.com",
                    sender_role="vendor",
                    subject="URGENT",
                    body="security issue",
                    priority_hint="high",
                    deadline_minutes=5,
                    business_value=0.8,
                    risk_tag="security",
                    thread_history=[],
                )
            ],
            time_remaining=60,
            pending_actions=[],
            risk_level="high",
            current_minute=0,
            persona="balanced",
            remaining_interruptions=0,
        )
        planner = Planner()
        strategy, metadata = planner.plan(obs)
        assert strategy == Strategy.ESCALATE_CRITICAL
        assert metadata["confidence"] == 1.0

    def test_plan_with_low_time_and_urgent_returns_prioritize_urgent(self) -> None:
        obs = Observation(
            emails=[
                ObservationEmail(
                    id="u1",
                    sender="boss@co.com",
                    sender_role="internal",
                    subject="Deadline",
                    body="Please review ASAP",
                    priority_hint="high",
                    deadline_minutes=2,
                    business_value=0.9,
                    risk_tag="none",
                    thread_history=[],
                )
            ],
            time_remaining=20,
            pending_actions=[],
            risk_level="low",
            current_minute=40,
            persona="balanced",
            remaining_interruptions=1,
        )
        planner = Planner()
        strategy, metadata = planner.plan(obs)
        assert strategy == Strategy.PRIORITIZE_URGENT
        assert metadata["reasoning"] == "Low time remaining with urgent emails pending"

    def test_plan_with_no_provider_key_returns_fallback(self, monkeypatch) -> None:
        monkeypatch.delenv("HF_TOKEN", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
        obs = Observation(
            emails=[
                ObservationEmail(
                    id="e1",
                    sender="x@y.com",
                    sender_role="client",
                    subject="Hello",
                    body="Normal email",
                    priority_hint="medium",
                    deadline_minutes=60,
                    business_value=0.5,
                    risk_tag="none",
                    thread_history=[],
                )
            ],
            time_remaining=60,
            pending_actions=[],
            risk_level="low",
            current_minute=0,
            persona="balanced",
            remaining_interruptions=0,
        )
        planner = Planner()
        strategy, metadata = planner.plan(obs)
        assert strategy == Strategy.PRIORITIZE_URGENT
        assert metadata["confidence"] == 0.3

    def test_reset_clears_current_strategy(self) -> None:
        planner = Planner()
        planner._current_strategy = Strategy.MONITOR
        planner.reset()
        assert planner._current_strategy is None


class TestModuleLevelFunctions:
    def test_get_strategy_returns_strategy_when_no_provider(self, monkeypatch) -> None:
        monkeypatch.delenv("HF_TOKEN", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
        obs = Observation(
            emails=[
                ObservationEmail(
                    id="e1",
                    sender="x@y.com",
                    sender_role="client",
                    subject="Test",
                    body="Normal",
                    priority_hint="medium",
                    deadline_minutes=60,
                    business_value=0.5,
                    risk_tag="none",
                    thread_history=[],
                )
            ],
            time_remaining=60,
            pending_actions=[],
            risk_level="low",
            current_minute=0,
            persona="balanced",
            remaining_interruptions=0,
        )
        strategy, metadata = get_strategy(obs)
        assert isinstance(strategy, Strategy)
        assert "reasoning" in metadata

    def test_reset_planner_works(self, monkeypatch) -> None:
        monkeypatch.delenv("HF_TOKEN", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
        obs = Observation(
            emails=[],
            time_remaining=60,
            pending_actions=[],
            risk_level="low",
            current_minute=0,
            persona="balanced",
            remaining_interruptions=0,
        )
        # get_strategy creates the default planner
        get_strategy(obs)
        reset_planner()

        from app.llm.policy import _default_planner

        assert _default_planner is None


class TestLLMPolicy:
    def test_next_action_returns_action(self, monkeypatch) -> None:
        monkeypatch.delenv("HF_TOKEN", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
        obs = Observation(
            emails=[
                ObservationEmail(
                    id="e1",
                    sender="x@y.com",
                    sender_role="client",
                    subject="Test",
                    body="Hello",
                    priority_hint="medium",
                    deadline_minutes=60,
                    business_value=0.5,
                    risk_tag="none",
                    thread_history=[],
                )
            ],
            time_remaining=60,
            pending_actions=[],
            risk_level="low",
            current_minute=0,
            persona="balanced",
            remaining_interruptions=0,
        )
        policy = LLMPolicy()
        action = policy.next_action(obs)
        # Should not crash; returns an Action (which may be a fallback)
        assert action is not None

    def test_reset_clears_state(self) -> None:
        policy = LLMPolicy()
        policy._handled_ids.add("e1")
        with patch("app.llm.agent.reset_agent") as mock_reset:
            policy.reset()
        assert len(policy._handled_ids) == 0
        mock_reset.assert_called_once()
