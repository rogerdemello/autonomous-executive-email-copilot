from __future__ import annotations

import time
from collections.abc import AsyncGenerator, Generator
from typing import Any

from openai import AsyncOpenAI, OpenAI

from app.core.config import chat_client_kwargs

from .base import LLMChunk, LLMProvider, LLMResponse, ProviderCapability, ToolCall


class OpenAIProvider(LLMProvider):
    provider_name = "openai"

    def __init__(
        self,
        model: str | None = None,
        temperature: float = 0.2,
        timeout_seconds: float = 30.0,
    ):
        self._model = model
        self._temperature = temperature
        self._timeout_seconds = timeout_seconds
        self._client: OpenAI | None = None
        self._async_client: AsyncOpenAI | None = None

    @property
    def capabilities(self) -> set[str]:
        return {
            ProviderCapability.STRUCTURED_OUTPUT,
            ProviderCapability.TOOLS,
            ProviderCapability.STREAMING,
            ProviderCapability.FUNCTION_CALLING,
        }

    def _get_client(self) -> OpenAI:
        if self._client is None:
            kwargs, model = chat_client_kwargs(self._timeout_seconds)
            self._client = OpenAI(**kwargs)
            if model != self._model:
                self._model = model
        return self._client

    def _get_async_client(self) -> AsyncOpenAI:
        if self._async_client is None:
            kwargs, model = chat_client_kwargs(self._timeout_seconds)
            self._async_client = AsyncOpenAI(**kwargs)
            if model != self._model:
                self._model = model
        return self._async_client

    def _build_kwargs(
        self,
        model: str | None,
        temperature: float | None,
        max_tokens: int | None,
        response_format: dict[str, Any] | None,
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": model or self._model or "",
            "temperature": temperature if temperature is not None else self._temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if response_format is not None:
            kwargs["response_format"] = response_format
        if tools is not None:
            kwargs["tools"] = tools
        return kwargs

    def _parse_response(self, raw: Any, start_time: float) -> LLMResponse:
        latency_ms = int((time.time() - start_time) * 1000)
        choice = raw.choices[0]
        message = choice.message

        tool_calls = None
        if message.tool_calls:
            tool_calls = [
                ToolCall(
                    id=tc.id,
                    type=tc.type,
                    function_name=tc.function.name,
                    arguments=tc.function.arguments,
                )
                for tc in message.tool_calls
            ]

        usage = None
        if raw.usage:
            from app.core.models import TokenUsage

            usage = TokenUsage(
                prompt_tokens=raw.usage.prompt_tokens or 0,
                completion_tokens=raw.usage.completion_tokens or 0,
                total_tokens=raw.usage.total_tokens or 0,
            )

        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            usage=usage,
            model=raw.model,
            finish_reason=choice.finish_reason,
            latency_ms=latency_ms,
        )

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
        kwargs = self._build_kwargs(model, temperature, max_tokens, response_format, tools)
        raw = client.chat.completions.create(messages=messages, **kwargs)
        return self._parse_response(raw, start_time)

    def generate_stream(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> Generator[LLMChunk, None, LLMResponse]:
        start_time = time.time()
        client = self._get_client()
        kwargs = self._build_kwargs(model, temperature, max_tokens, response_format, tools)
        kwargs["stream"] = True
        stream = client.chat.completions.create(messages=messages, **kwargs)

        full_content: list[str] = []
        tool_call_deltas: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None

        for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue

            if delta.content:
                full_content.append(delta.content)
                yield LLMChunk(content=delta.content)

            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_call_deltas:
                        tool_call_deltas[idx] = {"id": "", "function_name": "", "arguments": ""}
                    if tc_delta.id:
                        tool_call_deltas[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tool_call_deltas[idx]["function_name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            tool_call_deltas[idx]["arguments"] += tc_delta.function.arguments

            if chunk.choices and chunk.choices[0].finish_reason:
                finish_reason = chunk.choices[0].finish_reason

        tool_calls = None
        if tool_call_deltas:
            tool_calls = [
                ToolCall(
                    id=info["id"],
                    function_name=info["function_name"],
                    arguments=info["arguments"],
                )
                for info in tool_call_deltas.values()
            ]

        yield LLMResponse(
            content="".join(full_content) if full_content else None,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            latency_ms=int((time.time() - start_time) * 1000),
        )

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
        kwargs = self._build_kwargs(model, temperature, max_tokens, response_format, tools)
        raw = await client.chat.completions.create(messages=messages, **kwargs)
        return self._parse_response(raw, start_time)

    async def agenerate_stream(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncGenerator[LLMChunk, None]:
        start_time = time.time()
        client = self._get_async_client()
        kwargs = self._build_kwargs(model, temperature, max_tokens, response_format, tools)
        kwargs["stream"] = True
        stream = await client.chat.completions.create(messages=messages, **kwargs)

        full_content: list[str] = []
        finish_reason: str | None = None

        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue

            if delta.content:
                full_content.append(delta.content)
                yield LLMChunk(content=delta.content)

            if chunk.choices and chunk.choices[0].finish_reason:
                finish_reason = chunk.choices[0].finish_reason

        yield LLMResponse(
            content="".join(full_content) if full_content else None,
            finish_reason=finish_reason,
            latency_ms=int((time.time() - start_time) * 1000),
        )
