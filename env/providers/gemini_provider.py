from __future__ import annotations

import time
from typing import Any

from .base import LLMProvider, LLMResponse, ProviderCapability


class GeminiProvider(LLMProvider):
    provider_name = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-1.5-flash",
        temperature: float = 0.2,
        timeout_seconds: float = 30.0,
    ):
        self._api_key = api_key
        self._model = model
        self._temperature = temperature
        self._timeout_seconds = timeout_seconds

    @property
    def capabilities(self) -> set[str]:
        return {
            ProviderCapability.STREAMING,
        }

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
        import google.generativeai as genai

        genai.configure(api_key=self._api_key)
        gen_model = genai.GenerativeModel(model or self._model)

        system_instruction = None
        chat_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_instruction = msg["content"]
            else:
                chat_messages.append({"role": msg["role"], "parts": [msg["content"]]})

        if system_instruction:
            gen_model = genai.GenerativeModel(
                model or self._model,
                system_instruction=system_instruction,
            )

        generation_config = {
            "temperature": temperature if temperature is not None else self._temperature,
        }
        if max_tokens:
            generation_config["max_output_tokens"] = max_tokens

        raw = gen_model.generate_content(
            chat_messages,
            generation_config=generation_config,
        )

        latency_ms = int((time.time() - start_time) * 1000)

        content = raw.text if raw.text else None
        usage = None

        return LLMResponse(
            content=content,
            usage=usage,
            model=model or self._model,
            finish_reason=raw.candidates[0].finish_reason.name if raw.candidates else None,
            latency_ms=latency_ms,
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
        return self.generate(messages, model, temperature, max_tokens, response_format, tools)
