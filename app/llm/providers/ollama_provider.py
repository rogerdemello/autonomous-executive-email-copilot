from __future__ import annotations

from openai import OpenAI

from .base import ProviderCapability
from .openai_provider import OpenAIProvider


class OllamaProvider(OpenAIProvider):
    provider_name = "ollama"

    def __init__(
        self,
        base_url: str = "http://localhost:11434/v1",
        model: str = "llama3.1",
        temperature: float = 0.2,
        timeout_seconds: float = 30.0,
    ):
        super().__init__(model=model, temperature=temperature, timeout_seconds=timeout_seconds)
        self._base_url = base_url
        self._client = OpenAI(
            base_url=base_url,
            api_key="ollama",
            timeout=timeout_seconds,
        )

    @property
    def capabilities(self) -> set[str]:
        return {
            ProviderCapability.STREAMING,
        }

    def _get_client(self) -> OpenAI:
        return self._client

    def _get_async_client(self):
        # Must be overridden alongside _get_client: the parent's async client
        # is built from the OpenAI settings, so an async call on a "local"
        # Ollama provider would silently send the prompt to api.openai.com
        # (or 401) instead of localhost.
        from openai import AsyncOpenAI

        return AsyncOpenAI(
            base_url=self._base_url,
            api_key="ollama",
            timeout=self._timeout_seconds,
        )
