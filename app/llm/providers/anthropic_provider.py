"""Anthropic (Claude) provider.

The agent layer speaks OpenAI shapes everywhere — tool definitions as
``{"type": "function", "function": {...}}`` and tool-call arguments as JSON
strings. This module owns the translation to and from Anthropic's API:

- tools become ``{"name", "description", "input_schema"}`` (`_convert_tools`);
- the system message becomes the top-level ``system`` parameter rather than a
  fake user turn (`_split_system`);
- ``tool_use`` block inputs are serialized with ``json.dumps`` so the shared
  ``app.llm.tools`` parser (which does ``json.loads``) can read them — a
  ``str(dict)`` repr with single quotes cannot be parsed and used to silently
  degrade every Anthropic tool call to the fallback path.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncGenerator, Generator
from typing import Any

from .base import LLMChunk, LLMProvider, LLMResponse, ProviderCapability, ToolCall


def _convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI-shaped tool definitions → Anthropic shape (idempotent)."""
    converted = []
    for tool in tools:
        if "function" in tool:  # OpenAI: {"type": "function", "function": {...}}
            fn = tool["function"]
            converted.append(
                {
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {"type": "object"}),
                }
            )
        else:  # already Anthropic-shaped
            converted.append(tool)
    return converted


def _split_system(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    """Pull system messages out into Anthropic's top-level ``system`` param.

    Folding them into a user turn (the previous behaviour) produced two
    consecutive user messages and lost the instruction-following treatment
    system prompts get.
    """
    system_parts: list[str] = []
    rest: list[dict[str, Any]] = []
    for msg in messages:
        if msg["role"] == "system":
            system_parts.append(str(msg["content"]))
        else:
            rest.append({"role": msg["role"], "content": msg["content"]})
    return ("\n\n".join(system_parts) or None), rest


def _tool_calls_from_content(blocks: Any) -> tuple[str, list[ToolCall] | None]:
    content = ""
    tool_calls: list[ToolCall] | None = None
    for block in blocks:
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
                    # json.dumps, NOT str(): downstream parsing is json.loads.
                    arguments=json.dumps(block.input),
                )
            )
    return content, tool_calls


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
        # No STREAMING: generate_stream is NotImplemented, and advertising a
        # capability the implementation raises on is how callers break.
        return {
            ProviderCapability.TOOLS,
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

    def _build_kwargs(
        self,
        messages: list[dict[str, Any]],
        model: str | None,
        temperature: float,
        max_tokens: int | None,
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        system, converted = _split_system(messages)
        kwargs: dict[str, Any] = {
            "model": model or self._model,
            "messages": converted,
            "temperature": temperature if temperature is not None else self._temperature,
            "max_tokens": max_tokens or 1024,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = _convert_tools(tools)
        return kwargs

    def _to_response(self, raw: Any, latency_ms: int) -> LLMResponse:
        content, tool_calls = _tool_calls_from_content(raw.content)
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
        raw = client.messages.create(
            **self._build_kwargs(messages, model, temperature, max_tokens, tools)
        )
        return self._to_response(raw, int((time.time() - start_time) * 1000))

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
        raw = await client.messages.create(
            **self._build_kwargs(messages, model, temperature, max_tokens, tools)
        )
        return self._to_response(raw, int((time.time() - start_time) * 1000))

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
