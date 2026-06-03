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

    # Security (all opt-in; the API runs open by default)
    api_auth_token: str | None = None
    cors_origins: str = "*"
    rate_limit_per_minute: int = 0  # 0 disables rate limiting

    # Dashboard / UI
    app_api_base_url: str = "http://localhost:8000"

    # Logging
    log_level: str = "INFO"

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS origins as a list; '*' (or empty) means allow all."""
        raw = (self.cors_origins or "*").strip()
        if raw == "*":
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]

    @property
    def resolved_api_key(self) -> str | None:
        """Provider key, preferring HF_TOKEN then OPENAI_API_KEY (legacy order)."""
        return self.hf_token or self.openai_api_key


def get_settings() -> Settings:
    """Build a Settings object from the current environment."""
    return Settings()


def is_azure_endpoint(api_base_url: str | None) -> bool:
    """True if the base URL points at an Azure OpenAI resource."""
    host = (urlsplit((api_base_url or "").strip()).netloc or "").lower()
    return "openai.azure.com" in host


def chat_client_kwargs(timeout_seconds: float = 30.0) -> tuple[dict, str]:
    """Compute keyword args for an OpenAI client + the resolved model name.

    Returns ``(kwargs, model_name)``. Works for both public OpenAI-compatible
    endpoints and **Azure OpenAI**. Azure authenticates with an ``api-key``
    request header (not ``Authorization: Bearer``) and pins the deployment in the
    URL path, so for Azure hosts we inject the ``api-key`` header explicitly —
    otherwise key-based auth 401s.

    Construction is left to the caller (each module instantiates its own
    ``OpenAI`` so unit tests can patch it locally).
    """
    settings = get_settings()
    api_base_url = normalize_openai_base_url(settings.api_base_url, settings.azure_api_version)
    api_key = settings.resolved_api_key
    if not api_key:
        raise ValueError("HF_TOKEN or OPENAI_API_KEY environment variable not set")

    kwargs: dict = {
        "base_url": api_base_url,
        "api_key": api_key,
        "timeout": timeout_seconds,
    }
    if is_azure_endpoint(api_base_url):
        # Azure validates the resource key via the `api-key` header.
        kwargs["default_headers"] = {"api-key": api_key}

    return kwargs, (settings.model_name or DEFAULT_MODEL)


def build_chat_client(timeout_seconds: float = 30.0):  # type: ignore[no-untyped-def]
    """Construct an OpenAI/Azure client wired for the configured provider.

    Convenience wrapper over :func:`chat_client_kwargs` for direct callers.
    Returns ``(client, model_name)``.
    """
    from openai import OpenAI

    kwargs, model = chat_client_kwargs(timeout_seconds)
    return OpenAI(**kwargs), model


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
