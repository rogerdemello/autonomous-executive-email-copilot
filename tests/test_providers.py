"""Multi-provider support via the OpenAI-compatible client path.

The LLM agent uses an OpenAI-compatible client, so any OpenAI-compatible endpoint
(OpenAI, Azure OpenAI, Groq, local Ollama, or Anthropic's OpenAI-compatible
endpoint) works by pointing API_BASE_URL/MODEL_NAME at the provider. These tests
lock that ``chat_client_kwargs`` honors a custom base_url + model for non-Azure
providers, so swapping providers needs no code change.
"""

from __future__ import annotations

from env.config import chat_client_kwargs


def _clear_provider_env(monkeypatch) -> None:
    for var in ("HF_TOKEN", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_groq_style_base_url_is_honored(monkeypatch) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "gsk_test")
    monkeypatch.setenv("API_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setenv("MODEL_NAME", "llama-3.3-70b-versatile")

    kwargs, model = chat_client_kwargs()
    assert kwargs["base_url"] == "https://api.groq.com/openai/v1"
    assert kwargs["api_key"] == "gsk_test"
    assert model == "llama-3.3-70b-versatile"


def test_local_ollama_base_url_is_honored(monkeypatch) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "ollama")
    monkeypatch.setenv("API_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("MODEL_NAME", "llama3.1")

    kwargs, model = chat_client_kwargs()
    assert kwargs["base_url"] == "http://localhost:11434/v1"
    assert model == "llama3.1"


def test_default_is_openai(monkeypatch) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.delenv("API_BASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk_test")
    kwargs, _ = chat_client_kwargs()
    assert kwargs["base_url"] == "https://api.openai.com/v1"
