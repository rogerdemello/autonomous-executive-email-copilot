from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Generator
from dataclasses import dataclass
from typing import Any

from env.models import TokenUsage


class ProviderCapability:
    STRUCTURED_OUTPUT = "structured_output"
    TOOLS = "tools"
    STREAMING = "streaming"
    FUNCTION_CALLING = "function_calling"


@dataclass
class ToolCall:
    id: str
    type: str = "function"
    function_name: str = ""
    arguments: str = ""


@dataclass
class LLMResponse:
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    usage: TokenUsage | None = None
    model: str = ""
    finish_reason: str | None = None
    latency_ms: int = 0


@dataclass
class LLMChunk:
    content: str | None = None
    tool_call_delta: dict | None = None
    finish_reason: str | None = None


MODEL_PRICING: dict[str, dict[str, float]] = {
    # OpenAI
    "gpt-4o-mini": {"prompt": 0.15, "completion": 0.60},
    "gpt-4o": {"prompt": 2.50, "completion": 10.00},
    "gpt-4": {"prompt": 30.00, "completion": 60.00},
    "gpt-4-turbo": {"prompt": 10.00, "completion": 30.00},
    "o1-mini": {"prompt": 1.10, "completion": 4.40},
    "o1-preview": {"prompt": 15.00, "completion": 60.00},
    # Anthropic
    "claude-3-5-sonnet-20241022": {"prompt": 3.00, "completion": 15.00},
    "claude-3-5-haiku-20241022": {"prompt": 0.80, "completion": 4.00},
    "claude-3-opus-20240229": {"prompt": 15.00, "completion": 75.00},
    # Google
    "gemini-1.5-pro": {"prompt": 1.25, "completion": 5.00},
    "gemini-1.5-flash": {"prompt": 0.075, "completion": 0.30},
    "gemini-2.0-flash": {"prompt": 0.10, "completion": 0.40},
}


def calculate_cost(model: str, usage: TokenUsage) -> float:
    pricing = MODEL_PRICING.get(model, {"prompt": 0.0, "completion": 0.0})
    prompt_cost = (usage.prompt_tokens / 1_000_000) * pricing["prompt"]
    completion_cost = (usage.completion_tokens / 1_000_000) * pricing["completion"]
    return prompt_cost + completion_cost


class LLMProvider(ABC):
    provider_name: str = ""

    @property
    def capabilities(self) -> set[str]:
        return set()

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    @abstractmethod
    def generate(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse: ...

    def generate_stream(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> Generator[LLMChunk, None, LLMResponse]:
        raise NotImplementedError(f"{self.provider_name} does not support streaming")

    async def agenerate(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        return self.generate(messages, model, temperature, max_tokens, response_format, tools)

    async def agenerate_stream(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncGenerator[LLMChunk, None]:
        raise NotImplementedError(f"{self.provider_name} does not support async streaming")
