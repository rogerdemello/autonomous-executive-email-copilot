"""Tests for centralized configuration (env/config.py)."""

from __future__ import annotations

import pytest

from env.config import (
    DEFAULT_API_BASE_URL,
    DEFAULT_MODEL,
    get_settings,
    normalize_openai_base_url,
)


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
