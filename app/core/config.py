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

# A deterministic, obviously-not-secret fallback so local dev and tests work with
# zero config. Production MUST set AUTH_SECRET_KEY (see Settings.auth_secret_is_dev).
DEV_AUTH_SECRET = "dev-insecure-secret-do-not-use-in-production"


class Settings(BaseSettings):
    """Typed view over the process environment (and an optional .env file)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Provider selection (auto-detected if not set)
    llm_provider: str | None = None  # "openai" | "anthropic" | "gemini" | "ollama"

    # Provider credentials
    openai_api_key: str | None = None
    hf_token: str | None = None
    anthropic_api_key: str | None = None
    google_api_key: str | None = None
    ollama_base_url: str | None = None

    # Endpoint / model selection
    api_base_url: str = DEFAULT_API_BASE_URL
    model_name: str = DEFAULT_MODEL
    larger_model: str = DEFAULT_LARGER_MODEL
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD
    azure_api_version: str = DEFAULT_AZURE_API_VERSION

    # Native Azure OpenAI block (standard AZURE_OPENAI_* names). When the endpoint
    # and chat deployment are set, these take precedence and the app derives the
    # base URL / key / version / model automatically — no need to also set
    # API_BASE_URL / OPENAI_API_KEY / MODEL_NAME.
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: str | None = None
    azure_openai_api_version: str | None = None
    azure_openai_deployment_name: str | None = None
    azure_openai_embedding_deployment: str | None = None

    # Database
    # Optional SQLAlchemy URL. When unset (default) the app uses a zero-config
    # local SQLite database. Set to a Postgres URL (e.g.
    # ``postgresql+psycopg://user:pass@host:5432/db``) to run on Postgres; the
    # driver (``pip install psycopg[binary]``) is an optional dependency.
    database_url: str | None = None

    # Behavior
    require_approval: bool = False

    # How many messages one sync pulls. The provider interface defaults to 25,
    # which silently truncated a 50-message mailbox to its first half — the
    # inbox looked complete because nothing reports what was left behind.
    inbox_sync_limit: int = 100

    # Call the model to write reply/escalation prose during an inbox sync.
    # Off by default, deliberately: a sync is request-bound, and a slow or
    # unreachable provider would stall the user rather than the background job.
    # Cached drafts are always used regardless of this flag, so a seeded demo
    # shows model-written prose with this off and no network available.
    llm_drafting_enabled: bool = False

    # Background sync worker. Off by default so tests and one-shot scripts get
    # no surprise threads; a deployment turns it on to get "the copilot worked
    # your inbox while you slept" instead of sync-on-click. Each connection
    # syncs when its last_synced_at is older than the interval plus a stable
    # per-connection jitter (so a fleet doesn't hit provider APIs in lockstep);
    # the poll is how often the worker checks for due connections.
    sync_worker_enabled: bool = False
    sync_worker_interval_seconds: int = 300
    sync_worker_jitter_fraction: float = 0.2
    sync_worker_poll_seconds: int = 30

    # Scenario selection
    # When False (default) the loader resolves exactly ``{task_id}.yaml`` so
    # golden scores stay byte-identical. When True the loader globs
    # ``{task_id}*.yaml`` (canonical + variants) and picks one deterministically
    # from the seed.
    scenario_variants: bool = False

    # Security (all opt-in; the API runs open by default)
    api_auth_token: str | None = None
    # Optional multi-tenant token map in the form
    # ``token1:tenantA,token2:tenantB``. Each token authenticates exactly like
    # ``api_auth_token`` but additionally resolves a tenant label used for the
    # ``X-Tenant`` response header and per-tenant rate-limit keying. Full
    # per-tenant DB row isolation is intentionally OUT OF SCOPE here (no tenant
    # column is added); treat that as a follow-up.
    api_tenants: str | None = None
    cors_origins: str = "*"
    rate_limit_per_minute: int = 0  # 0 disables rate limiting

    # --- Commercial SaaS layer (accounts, tenants, licensing) ---
    # Deployment environment: "development" (default) | "production". In
    # production the app hard-fails at startup on unsafe config (e.g. a missing
    # AUTH_SECRET_KEY) instead of silently using an insecure default.
    environment: str = "development"
    # Secret used to sign session tokens and license keys (HS256). Leave unset
    # for local dev/tests (a clearly-marked, non-production fallback is used and
    # a warning is logged). MUST be set to a long random value in production —
    # rotating it invalidates all outstanding tokens and licenses.
    auth_secret_key: str | None = None

    # --- SSO (OIDC single sign-on) ---
    # Server-level OpenID Connect. When issuer + client id/secret are set, the
    # "Sign in with SSO" flow is available (/auth/sso/login). id_tokens are
    # verified against the issuer's JWKS. Per-organization SSO is a follow-up.
    oidc_issuer: str | None = None  # e.g. https://accounts.google.com
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    # Lifetime of an issued login token, in minutes (default 12h).
    access_token_ttl_minutes: int = 720
    # Lifetime of a password-reset link, in minutes (default 1h).
    password_reset_ttl_minutes: int = 60
    # Public base URL of the app, used to build user-facing links (password reset,
    # invites). Falls back to the OAuth redirect base or the API base URL.
    app_public_url: str | None = None

    # --- Transactional email (password reset, invites) ---
    # "console" (default) logs emails instead of sending — zero-config for local
    # dev and tests. "smtp" sends via the SMTP_* settings below.
    email_provider: str = "console"
    email_from: str = "no-reply@example.com"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = True
    # Self-serve signup toggle. When False, only an operator can provision orgs
    # (pure sales-led onboarding). Default True so the trial funnel works.
    signup_enabled: bool = True
    # Where "Contact sales" leads and license requests are announced (optional
    # webhook, e.g. Slack incoming webhook). When unset, leads are persisted +
    # logged only.
    sales_webhook_url: str | None = None
    sales_contact_email: str = "sales@example.com"

    # --- Mailbox OAuth (connect real Gmail / Microsoft 365 inboxes) ---
    # Base URL of the deployed app, used to build the OAuth redirect URI
    # (``<base>/mailbox/oauth/callback``). When unset it is derived from the
    # incoming request. Set it in production so it matches the value registered
    # with Google/Microsoft.
    oauth_redirect_base_url: str | None = None
    # Google Cloud OAuth client (Gmail). A provider is "available" only when both
    # its id and secret are set — otherwise the connect endpoint 400s.
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: str | None = None
    # Microsoft Entra (Azure AD) app registration for Microsoft 365 mail.
    microsoft_oauth_client_id: str | None = None
    microsoft_oauth_client_secret: str | None = None
    microsoft_oauth_tenant: str = "common"

    # Dashboard / UI
    app_api_base_url: str = "http://localhost:8000"

    # Logging
    log_level: str = "INFO"

    # Observability: OTLP trace export target (e.g. http://tempo:4318).
    # Must be a declared field — model_config uses extra="ignore", so before
    # this existed the env var was silently swallowed and OTLP could never be
    # enabled despite being documented and set by the Helm chart.
    otel_exporter_otlp_endpoint: str | None = None

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS origins as a list; '*' (or empty) means allow all."""
        raw = (self.cors_origins or "*").strip()
        if raw == "*":
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]

    @property
    def tenant_token_map(self) -> dict[str, str]:
        """Parse ``api_tenants`` into a ``{token: tenant}`` mapping.

        Format is ``token1:tenantA,token2:tenantB``. Malformed or empty
        entries (missing token or tenant) are skipped. Returns an empty dict
        when unset, so multi-tenant auth stays fully opt-in.
        """
        raw = (self.api_tenants or "").strip()
        if not raw:
            return {}
        mapping: dict[str, str] = {}
        for pair in raw.split(","):
            token, sep, tenant = pair.partition(":")
            if not sep:
                continue
            token = token.strip()
            tenant = tenant.strip()
            if token and tenant:
                mapping[token] = tenant
        return mapping

    @property
    def resolved_auth_secret(self) -> str:
        """The signing secret for tokens/licenses, or a dev fallback."""
        secret = (self.auth_secret_key or "").strip()
        return secret or DEV_AUTH_SECRET

    @property
    def auth_secret_is_dev(self) -> bool:
        """True when no real AUTH_SECRET_KEY is configured (dev fallback in use)."""
        return not (self.auth_secret_key or "").strip()

    @property
    def is_production(self) -> bool:
        return (self.environment or "").strip().lower() == "production"

    @property
    def sso_enabled(self) -> bool:
        """True when server-level OIDC SSO is fully configured."""
        return bool(
            (self.oidc_issuer or "").strip()
            and (self.oidc_client_id or "").strip()
            and (self.oidc_client_secret or "").strip()
        )

    @property
    def resolved_app_public_url(self) -> str:
        """Public base URL for user-facing links (password reset, invites)."""
        for candidate in (self.app_public_url, self.oauth_redirect_base_url, self.app_api_base_url):
            if candidate and candidate.strip():
                return candidate.strip().rstrip("/")
        return "http://localhost:8000"

    @property
    def resolved_api_key(self) -> str | None:
        """Provider key: HF_TOKEN, then OPENAI_API_KEY, then AZURE_OPENAI_API_KEY."""
        return self.hf_token or self.openai_api_key or self.azure_openai_api_key

    @property
    def provider_available(self) -> bool:
        """True when any provider credential is configured."""
        return bool(
            self.resolved_api_key
            or self.anthropic_api_key
            or self.google_api_key
            or self.ollama_base_url
        )


def get_settings() -> Settings:
    """Build a Settings object from the current environment."""
    return Settings()


def is_azure_endpoint(api_base_url: str | None) -> bool:
    """True if the base URL points at an Azure OpenAI resource.

    Suffix-anchored on the host, not a substring test: a substring would also
    match ``openai.azure.com.evil.net`` and route the API key there.
    """
    host = (urlsplit((api_base_url or "").strip()).netloc or "").lower().rsplit(":", 1)[0]
    return host == "openai.azure.com" or host.endswith(".openai.azure.com")


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

    # Native AZURE_OPENAI_* block wins when an endpoint + chat deployment are set:
    # derive the deployment base URL, key, version, and model from it.
    if settings.azure_openai_endpoint and settings.azure_openai_deployment_name:
        endpoint = settings.azure_openai_endpoint.rstrip("/")
        deployment = settings.azure_openai_deployment_name
        api_base_url = f"{endpoint}/openai/deployments/{deployment}"
        api_key = settings.azure_openai_api_key or settings.resolved_api_key
        api_version = settings.azure_openai_api_version or settings.azure_api_version
        model = deployment
    else:
        api_base_url = normalize_openai_base_url(settings.api_base_url, settings.azure_api_version)
        api_key = settings.resolved_api_key
        api_version = settings.azure_api_version
        model = settings.model_name or DEFAULT_MODEL

    if not api_key:
        raise ValueError("No provider key set (OPENAI_API_KEY / AZURE_OPENAI_API_KEY / HF_TOKEN)")

    kwargs: dict = {
        "base_url": api_base_url,
        "api_key": api_key,
        "timeout": timeout_seconds,
    }
    if is_azure_endpoint(api_base_url):
        # Azure validates the resource key via the `api-key` header (not Bearer).
        # Critically, the base_url must be the bare deployment path WITHOUT a query
        # string: the OpenAI SDK resolves the request path relative to base_url and
        # an embedded `?api-version=...` corrupts that join (-> 404). So strip the
        # query and supply `api-version` via default_query, which rides on every
        # request.
        parts = urlsplit(api_base_url)
        api_version = dict(parse_qsl(parts.query)).get("api-version", api_version)
        kwargs["base_url"] = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        kwargs["default_headers"] = {"api-key": api_key}
        kwargs["default_query"] = {"api-version": api_version}

    return kwargs, model


def build_chat_client(timeout_seconds: float = 30.0):
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
