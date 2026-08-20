"""The multi-provider layer's contract, pinned after the overhaul.

Every test here guards a defect that used to make the "works with OpenAI,
Azure, Anthropic, Gemini, and local models" claim false on the non-OpenAI
paths: OpenAI-shaped tools sent to Anthropic raw, tool arguments serialized
as a Python dict repr, the OpenAI default model forced onto every provider,
a circuit wrapper whose async path ran the sync client, and failover that
was documented but never constructed.

No provider SDK is required: everything exercises the pure translation
helpers and the detection/wrapping logic, which import lazily.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.core.config import get_settings
from app.llm.providers import (
    AllProvidersFailedError,
    CircuitBreaker,
    CircuitBreakingProvider,
    _detect_provider,
    _detect_secondary,
    explicit_model,
)
from app.llm.providers.anthropic_provider import (
    AnthropicProvider,
    _convert_tools,
    _split_system,
    _tool_calls_from_content,
)
from app.llm.providers.base import LLMProvider, LLMResponse
from app.llm.tools import TOOL_DEFINITIONS, parse_tool_call_to_action


def _clear_llm_env(monkeypatch) -> None:
    for var in (
        "OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_DEPLOYMENT_NAME",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "OLLAMA_BASE_URL",
        "LLM_PROVIDER",
        "MODEL_NAME",
        "HF_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)


# --------------------------------------------------------------------------- #
# Anthropic translation
# --------------------------------------------------------------------------- #
class TestAnthropicTranslation:
    def test_openai_tools_become_input_schema_shape(self):
        converted = _convert_tools(TOOL_DEFINITIONS)
        assert len(converted) == len(TOOL_DEFINITIONS)
        for tool in converted:
            assert set(tool) == {"name", "description", "input_schema"}
            assert "function" not in tool  # the OpenAI wrapper must be gone

    def test_already_anthropic_shaped_tools_pass_through(self):
        native = [{"name": "x", "description": "d", "input_schema": {"type": "object"}}]
        assert _convert_tools(native) == native

    def test_system_message_becomes_top_level_param(self):
        system, rest = _split_system(
            [
                {"role": "system", "content": "You are terse."},
                {"role": "user", "content": "hi"},
            ]
        )
        assert system == "You are terse."
        # No fake user turn carrying the system prompt.
        assert rest == [{"role": "user", "content": "hi"}]

    def test_tool_use_arguments_round_trip_through_the_shared_parser(self):
        """str(block.input) produced a single-quoted dict repr that json.loads
        cannot read — every Anthropic tool call silently degraded to defer."""

        class Block:
            type = "tool_use"
            id = "tu_1"
            name = "classify"

            input = {"email_id": "msg_001", "label": "urgent", "confidence": 0.9}

        _content, tool_calls = _tool_calls_from_content([Block()])
        assert tool_calls is not None
        call = tool_calls[0]
        assert json.loads(call.arguments)["email_id"] == "msg_001"  # valid JSON, not repr

        action = parse_tool_call_to_action(call.function_name, call.arguments)
        assert action is not None and action.action_type == "classify"
        assert action.email_id == "msg_001"

    def test_streaming_is_not_advertised(self):
        provider = AnthropicProvider(api_key="k")
        from app.llm.providers.base import ProviderCapability

        assert ProviderCapability.STREAMING not in provider.capabilities
        assert ProviderCapability.TOOLS in provider.capabilities


# --------------------------------------------------------------------------- #
# Model-name resolution and provider precedence
# --------------------------------------------------------------------------- #
class TestModelResolution:
    def test_model_name_default_is_not_explicit(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        assert explicit_model(get_settings()) is None

    def test_model_name_env_is_explicit(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("MODEL_NAME", "claude-3-5-haiku-20241022")
        assert explicit_model(get_settings()) == "claude-3-5-haiku-20241022"

    def test_anthropic_key_alone_gets_a_claude_model(self, monkeypatch):
        """An Anthropic key used to yield model=gpt-4o-mini — a guaranteed 404
        unless MODEL_NAME was also set."""
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        provider = _detect_provider(get_settings())
        assert provider.provider_name == "anthropic"
        assert provider._model.startswith("claude")

    def test_explicit_model_name_still_wins(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        monkeypatch.setenv("MODEL_NAME", "claude-3-opus-20240229")
        provider = _detect_provider(get_settings())
        assert provider._model == "claude-3-opus-20240229"

    def test_openai_outranks_anthropic_when_both_configured(self, monkeypatch):
        """With several keys set, a deployment must not silently switch
        providers; the complete implementation (OpenAI) wins, and an explicit
        LLM_PROVIDER overrides."""
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        assert _detect_provider(get_settings()).provider_name == "openai"

        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        assert _detect_provider(get_settings()).provider_name == "anthropic"

    def test_secondary_failover_is_actually_wired(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        primary = _detect_provider(get_settings())
        secondary = _detect_secondary(primary, get_settings())
        assert secondary is not None and secondary.provider_name == "openai"


# --------------------------------------------------------------------------- #
# Circuit breaker
# --------------------------------------------------------------------------- #
class _FlakyProvider(LLMProvider):
    provider_name = "flaky"

    def __init__(self, fail: bool):
        self.fail = fail
        self.sync_calls = 0
        self.async_calls = 0

    @property
    def capabilities(self):
        return set()

    def generate(self, messages, **kwargs) -> LLMResponse:
        self.sync_calls += 1
        if self.fail:
            raise RuntimeError("boom")
        return LLMResponse(content="ok", model="m", latency_ms=1)

    async def agenerate(self, messages, **kwargs) -> LLMResponse:
        self.async_calls += 1
        if self.fail:
            raise RuntimeError("boom")
        return LLMResponse(content="ok-async", model="m", latency_ms=1)

    def generate_stream(self, messages, **kwargs):  # pragma: no cover
        raise NotImplementedError


class TestCircuitBreakingProvider:
    def test_agenerate_uses_the_async_path(self):
        """The wrapper had no agenerate, so async callers fell through to the
        base class, which runs the *sync* client on the event loop."""
        primary = _FlakyProvider(fail=False)
        wrapped = CircuitBreakingProvider(primary=primary)
        response = asyncio.run(wrapped.agenerate([{"role": "user", "content": "x"}]))
        assert response.content == "ok-async"
        assert primary.async_calls == 1
        assert primary.sync_calls == 0

    def test_failover_to_secondary_and_all_failed_error(self):
        primary = _FlakyProvider(fail=True)
        secondary = _FlakyProvider(fail=False)
        wrapped = CircuitBreakingProvider(primary=primary, secondary=secondary)
        response = wrapped.generate([{"role": "user", "content": "x"}])
        assert response.content == "ok"

        both_down = CircuitBreakingProvider(
            primary=_FlakyProvider(fail=True), secondary=_FlakyProvider(fail=True)
        )
        with pytest.raises(AllProvidersFailedError):
            both_down.generate([{"role": "user", "content": "x"}])

    def test_open_circuit_without_secondary_raises_all_failed(self):
        breaker = CircuitBreaker(name="t", failure_threshold=1, recovery_timeout=999)
        wrapped = CircuitBreakingProvider(primary=_FlakyProvider(fail=True), breaker=breaker)
        with pytest.raises(RuntimeError):
            wrapped.generate([{"role": "user", "content": "x"}])  # trips the breaker
        with pytest.raises(AllProvidersFailedError):
            wrapped.generate([{"role": "user", "content": "x"}])  # circuit now open


# --------------------------------------------------------------------------- #
# The agent's model pair per provider family
# --------------------------------------------------------------------------- #
class TestAgentModelPair:
    def test_openai_family_escalates_to_larger_model(self, monkeypatch):
        from app.llm.agent import LLMAgent
        from app.llm.providers.openai_provider import OpenAIProvider

        _clear_llm_env(monkeypatch)
        small, large = LLMAgent._models_for(OpenAIProvider(), get_settings())
        assert small == "gpt-4o-mini"
        assert large == "gpt-4o"

    def test_anthropic_family_never_receives_openai_model_names(self, monkeypatch):
        from app.llm.agent import LLMAgent

        _clear_llm_env(monkeypatch)
        provider = AnthropicProvider(api_key="k")
        small, large = LLMAgent._models_for(provider, get_settings())
        assert small.startswith("claude")
        assert large == small  # no cross-family escalation
