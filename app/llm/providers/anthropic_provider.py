from __future__ import annotations

import time
from collections.abc import AsyncGenerator, Generator
from typing import Any

from .base import LLMChunk, LLMProvider, LLMResponse, ProviderCapability, ToolCall


class AnthropicProvider(LLMProvider):
    provider_name = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str = "claude-3-5-sonnet-20241022",
        temperature: float = 0.2,
        timeout_seconds: float = 30.0,
    ):
        self._api_key = api_key
        self._model = model
        self._temperature = temperature
        self._timeout_seconds = timeout_seconds
        self._client = None

    @property
    def capabilities(self) -> set[str]:
        return {
            ProviderCapability.TOOLS,
            ProviderCapability.STREAMING,
            ProviderCapability.FUNCTION_CALLING,
        }

    def _get_client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(api_key=self._api_key, timeout=self._timeout_seconds)
        return self._client

    def _get_async_client(self):
        import anthropic

        return anthropic.AsyncAnthropic(api_key=self._api_key, timeout=self._timeout_seconds)

    def _convert_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        converted = []
        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            if role == "system":
                converted.append({"role": "user", "content": content})
            else:
                converted.append({"role": role, "content": content})
        return converted

    def generate(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        start_time = time.time()
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "model": model or self._model,
            "messages": self._convert_messages(messages),
            "temperature": temperature if temperature is not None else self._temperature,
            "max_tokens": max_tokens or 1024,
        }
        if tools:
            kwargs["tools"] = tools

        raw = client.messages.create(**kwargs)
        latency_ms = int((time.time() - start_time) * 1000)

        content = ""
        tool_calls = None
        for block in raw.content:
            if block.type == "text":
                content = block.text
            elif block.type == "tool_use":
                if tool_calls is None:
                    tool_calls = []
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        type="function",
                        function_name=block.name,
                        arguments=str(block.input),
                    )
                )

        usage = None
        if raw.usage:
            from app.core.models import TokenUsage

            usage = TokenUsage(
                prompt_tokens=raw.usage.input_tokens,
                completion_tokens=raw.usage.output_tokens,
                total_tokens=raw.usage.input_tokens + raw.usage.output_tokens,
            )

        return LLMResponse(
            content=content or None,
            tool_calls=tool_calls,
            usage=usage,
            model=raw.model,
            finish_reason=raw.stop_reason,
            latency_ms=latency_ms,
        )

    def generate_stream(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> Generator[LLMChunk, None, LLMResponse]:
        raise NotImplementedError("Anthropic sync streaming not yet implemented")

    async def agenerate(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        start_time = time.time()
        client = self._get_async_client()
        kwargs: dict[str, Any] = {
            "model": model or self._model,
            "messages": self._convert_messages(messages),
            "temperature": temperature if temperature is not None else self._temperature,
            "max_tokens": max_tokens or 1024,
        }
        if tools:
            kwargs["tools"] = tools

        raw = await client.messages.create(**kwargs)
        latency_ms = int((time.time() - start_time) * 1000)

        content = ""
        tool_calls = None
        for block in raw.content:
            if block.type == "text":
                content = block.text
            elif block.type == "tool_use":
                if tool_calls is None:
                    tool_calls = []
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        type="function",
                        function_name=block.name,
                        arguments=str(block.input),
                    )
                )

        usage = None
        if raw.usage:
            from app.core.models import TokenUsage

            usage = TokenUsage(
                prompt_tokens=raw.usage.input_tokens,
                completion_tokens=raw.usage.output_tokens,
                total_tokens=raw.usage.input_tokens + raw.usage.output_tokens,
            )

        return LLMResponse(
            content=content or None,
            tool_calls=tool_calls,
            usage=usage,
            model=raw.model,
            finish_reason=raw.stop_reason,
            latency_ms=latency_ms,
        )

    async def agenerate_stream(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncGenerator[LLMChunk, None]:
        raise NotImplementedError("Anthropic async streaming not yet implemented")
