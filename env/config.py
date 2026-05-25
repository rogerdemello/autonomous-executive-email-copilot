"""Centralized application configuration.

All environment-driven settings live here so the rest of the codebase reads a
single typed object instead of scattered ``os.environ.get`` calls. See
``.env.example`` for the documented variables.

``get_settings()`` returns a *fresh* instance on each call so that runtime
environment changes (and test monkeypatching of ``os.environ``) are always
respected. Settings objects are cheap to build.
"""

from __future__ import annotations

import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_API_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_LARGER_MODEL = "gpt-4o"
DEFAULT_CONFIDENCE_THRESHOLD = 0.7
DEFAULT_AZURE_API_VERSION = "2024-02-15-preview"


class Settings(BaseSettings):
    """Typed view over the process environment (and an optional .env file)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Provider credentials
    openai_api_key: str | None = None
    hf_token: str | None = None

    # Endpoint / model selection
    api_base_url: str = DEFAULT_API_BASE_URL
    model_name: str = DEFAULT_MODEL
    larger_model: str = DEFAULT_LARGER_MODEL
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD
    azure_api_version: str = DEFAULT_AZURE_API_VERSION

    # Behavior
    require_approval: bool = False

    # Dashboard / UI
    app_api_base_url: str = "http://localhost:8000"

    # Logging
    log_level: str = "INFO"

    @property
    def resolved_api_key(self) -> str | None:
        """Provider key, preferring HF_TOKEN then OPENAI_API_KEY (legacy order)."""
        return self.hf_token or self.openai_api_key


def get_settings() -> Settings:
    """Build a Settings object from the current environment."""
    return Settings()


def normalize_openai_base_url(api_base_url: str, azure_api_version: str | None = None) -> str:
    """Normalize an API base URL for OpenAI and Azure OpenAI compatibility.

    For Azure endpoints, require an ``/openai/deployments/<deployment>`` path and
    ensure an ``api-version`` query parameter is present.
    """
    cleaned = (api_base_url or "").strip()
    if not cleaned:
        return DEFAULT_API_BASE_URL

    parsed = urlsplit(cleaned)
    host = (parsed.netloc or "").lower()

    if "openai.azure.com" not in host:
        return cleaned

    if "/openai/deployments/" not in parsed.path:
        raise ValueError(
            "Azure API_BASE_URL must include /openai/deployments/<deployment>. "
            "Example: https://<resource>.openai.azure.com/openai/deployments/<deployment>?api-version=2024-02-15-preview"
        )

    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if "api-version" not in query:
        query["api-version"] = azure_api_version or os.environ.get(
            "AZURE_API_VERSION", DEFAULT_AZURE_API_VERSION
        )

    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )
