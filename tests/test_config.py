"""Tests for centralized configuration (env/config.py)."""

from __future__ import annotations

import pytest

from env.config import (
    DEFAULT_API_BASE_URL,
    DEFAULT_MODEL,
    build_chat_client,
    get_settings,
    is_azure_endpoint,
    normalize_openai_base_url,
)

AZURE_URL = "https://res.openai.azure.com/openai/deployments/gpt4o?api-version=2024-02-15-preview"


def test_defaults(monkeypatch):
    for var in ("OPENAI_API_KEY", "HF_TOKEN", "API_BASE_URL", "MODEL_NAME", "REQUIRE_APPROVAL"):
        monkeypatch.delenv(var, raising=False)
    settings = get_settings()
    assert settings.api_base_url == DEFAULT_API_BASE_URL
    assert settings.model_name == DEFAULT_MODEL
    assert settings.require_approval is False
    assert settings.resolved_api_key is None


def test_env_override_and_fresh_read(monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "custom-model")
    monkeypatch.setenv("REQUIRE_APPROVAL", "true")
    settings = get_settings()
    assert settings.model_name == "custom-model"
    assert settings.require_approval is True


def test_resolved_api_key_prefers_hf_token(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("HF_TOKEN", "hf-key")
    assert get_settings().resolved_api_key == "hf-key"

    monkeypatch.delenv("HF_TOKEN", raising=False)
    assert get_settings().resolved_api_key == "openai-key"


def test_normalize_plain_url_passthrough():
    assert normalize_openai_base_url("https://api.openai.com/v1") == "https://api.openai.com/v1"
    assert normalize_openai_base_url("") == DEFAULT_API_BASE_URL


def test_normalize_azure_requires_deployment():
    with pytest.raises(ValueError):
        normalize_openai_base_url("https://res.openai.azure.com/")


def test_normalize_azure_adds_api_version():
    url = normalize_openai_base_url(
        "https://res.openai.azure.com/openai/deployments/gpt4",
        azure_api_version="2024-02-15-preview",
    )
    assert "api-version=2024-02-15-preview" in url


def test_is_azure_endpoint():
    assert is_azure_endpoint(AZURE_URL) is True
    assert is_azure_endpoint("https://api.openai.com/v1") is False
    assert is_azure_endpoint("") is False
    assert is_azure_endpoint(None) is False


def test_build_chat_client_requires_key(monkeypatch):
    for var in ("OPENAI_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ValueError):
        build_chat_client()


def test_build_chat_client_openai_no_api_key_header(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("API_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("MODEL_NAME", "gpt-4o-mini")
    client, model = build_chat_client()
    assert model == "gpt-4o-mini"
    # Public OpenAI authenticates via Bearer; no custom api-key header injected.
    assert "api-key" not in {k.lower() for k in client.default_headers}


def test_build_chat_client_azure_injects_api_key_header(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "az-secret")
    monkeypatch.setenv("API_BASE_URL", AZURE_URL)
    monkeypatch.setenv("MODEL_NAME", "gpt-4o")
    client, model = build_chat_client()
    assert model == "gpt-4o"
    # Azure key auth requires the `api-key` header, not Authorization: Bearer.
    headers = {k.lower(): v for k, v in client.default_headers.items()}
    assert headers.get("api-key") == "az-secret"
    # api-version must ride on default_query (the SDK drops base_url's query when
    # it appends the request path), and base_url must be the bare deployment path.
    assert dict(client.default_query).get("api-version") == "2024-02-15-preview"
    assert "?" not in str(client.base_url)
    assert str(client.base_url).rstrip("/").endswith("/openai/deployments/gpt4o")


def test_build_chat_client_native_azure_block(monkeypatch):
    # The standard AZURE_OPENAI_* names work natively (no API_BASE_URL/MODEL_NAME).
    for var in ("OPENAI_API_KEY", "HF_TOKEN", "API_BASE_URL", "MODEL_NAME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://res.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "az-native")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o")

    assert get_settings().resolved_api_key == "az-native"
    client, model = build_chat_client()
    assert model == "gpt-4o"
    assert str(client.base_url).rstrip("/").endswith("/openai/deployments/gpt-4o")
    assert "?" not in str(client.base_url)
    headers = {k.lower(): v for k, v in client.default_headers.items()}
    assert headers.get("api-key") == "az-native"
    assert dict(client.default_query).get("api-version") == "2024-12-01-preview"
