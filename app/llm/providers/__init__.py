from __future__ import annotations

from app.core.config import get_settings

from .base import LLMChunk, LLMProvider, LLMResponse, ProviderCapability, ToolCall, calculate_cost
from .circuit_breaker import AllProvidersFailedError, CircuitBreaker, CircuitBreakingProvider
from .openai_provider import OpenAIProvider

__all__ = [
    "LLMProvider",
    "LLMResponse",
    "LLMChunk",
    "ToolCall",
    "ProviderCapability",
    "calculate_cost",
    "create_provider",
    "auto_detect_provider",
    "register_provider",
    "CircuitBreaker",
    "CircuitBreakingProvider",
    "AllProvidersFailedError",
    "explicit_model",
]


_provider_registry: dict[str, type[LLMProvider]] = {}


def register_provider(name: str, cls: type[LLMProvider]) -> None:
    _provider_registry[name] = cls


def get_provider_class(name: str) -> type[LLMProvider] | None:
    return _provider_registry.get(name)


def list_providers() -> list[str]:
    return list(_provider_registry.keys())


def create_provider(
    name: str,
    **kwargs,
) -> LLMProvider:
    cls = get_provider_class(name)
    if cls is None:
        raise ValueError(f"Unknown provider: {name}. Available: {list_providers()}")
    return cls(**kwargs)


def auto_detect_provider(with_circuit_breaker: bool = True) -> LLMProvider:
    settings = get_settings()
    provider = _detect_provider(settings)
    if with_circuit_breaker:
        provider = _maybe_wrap_circuit_breaker(provider, settings)
    return provider


def explicit_model(settings) -> str | None:
    """``MODEL_NAME`` only when the operator actually set it.

    The pydantic default (``gpt-4o-mini``) is an *OpenAI* model name. Passing
    it unconditionally to every constructor overrode the Anthropic, Gemini and
    Ollama defaults with a model those providers 404 on — an Anthropic key
    alone could never work without also setting MODEL_NAME.
    """
    if "model_name" in settings.model_fields_set:
        return settings.model_name
    return None


def _detect_provider(settings) -> LLMProvider:
    explicit = (settings.llm_provider or "").strip().lower()
    if explicit and explicit in _provider_registry:
        kwargs = _resolve_provider_kwargs(explicit, settings)
        return _provider_registry[explicit](**kwargs)

    # OpenAI/Azure first: it is the one complete implementation (tools,
    # streaming, async), so with several keys configured a deployment must not
    # silently switch providers. An explicit LLM_PROVIDER always wins above.
    if settings.azure_openai_endpoint and settings.azure_openai_deployment_name:
        return _build_openai_provider(settings)

    if settings.openai_api_key:
        return _build_openai_provider(settings)

    model = explicit_model(settings)

    if settings.anthropic_api_key:
        from .anthropic_provider import AnthropicProvider

        kwargs = {"api_key": settings.anthropic_api_key, "timeout_seconds": 30.0}
        if model:
            kwargs["model"] = model
        return AnthropicProvider(**kwargs)

    if settings.google_api_key:
        from .gemini_provider import GeminiProvider

        kwargs = {"api_key": settings.google_api_key, "timeout_seconds": 30.0}
        if model:
            kwargs["model"] = model
        return GeminiProvider(**kwargs)

    if settings.ollama_base_url:
        from .ollama_provider import OllamaProvider

        kwargs = {"base_url": settings.ollama_base_url, "timeout_seconds": 30.0}
        if model:
            kwargs["model"] = model
        return OllamaProvider(**kwargs)

    if settings.resolved_api_key:
        return _build_openai_provider(settings)

    raise ValueError(
        "No LLM provider configured. Set OPENAI_API_KEY, ANTHROPIC_API_KEY, "
        "GOOGLE_API_KEY, or LLM_PROVIDER in your .env file."
    )


def _build_openai_provider(settings) -> OpenAIProvider:
    return OpenAIProvider(
        model=settings.model_name,
        timeout_seconds=30.0,
    )


def _maybe_wrap_circuit_breaker(provider: LLMProvider, settings) -> LLMProvider:
    """Wrap the provider with a circuit breaker unless disabled by env."""
    import os as _os

    if _os.environ.get("CIRCUIT_BREAKER_DISABLED", "").strip().lower() in ("1", "true", "yes"):
        return provider
    breaker = CircuitBreaker(
        name=provider.provider_name,
        failure_threshold=3,
        recovery_timeout=30.0,
    )
    return CircuitBreakingProvider(
        primary=provider,
        secondary=_detect_secondary(provider, settings),
        breaker=breaker,
    )


def _detect_secondary(primary: LLMProvider, settings) -> LLMProvider | None:
    """A failover provider from *other* configured credentials, if any.

    The documented failover ("if primary is open, try secondary") was never
    wired — the wrapper was always constructed with secondary=None. This picks
    the first configured provider of a different family than the primary.
    """
    primary_family = primary.provider_name.split("+")[0]
    try:
        # The secondary always runs its own family's default model: MODEL_NAME
        # names a model in the *primary's* family, and a cross-family failover
        # asked for it would 404 at exactly the moment failover matters.
        if primary_family != "openai" and (
            settings.openai_api_key
            or (settings.azure_openai_endpoint and settings.azure_openai_deployment_name)
        ):
            return OpenAIProvider(timeout_seconds=30.0)
        if primary_family != "anthropic" and settings.anthropic_api_key:
            from .anthropic_provider import AnthropicProvider

            return AnthropicProvider(api_key=settings.anthropic_api_key, timeout_seconds=30.0)
    except Exception:  # a broken secondary must never break primary detection
        return None
    return None


def _resolve_provider_kwargs(name: str, settings) -> dict:
    base: dict = {"temperature": 0.2, "timeout_seconds": 30.0}
    # Each provider class carries its own sensible model default; MODEL_NAME
    # overrides it only when the operator set it (see _explicit_model).
    model = explicit_model(settings)
    if name == "openai":
        base["model"] = settings.model_name
    elif model:
        base["model"] = model
    if name == "anthropic":
        base["api_key"] = settings.anthropic_api_key or settings.resolved_api_key or ""
    elif name == "gemini":
        base["api_key"] = settings.google_api_key or settings.resolved_api_key or ""
    elif name == "ollama":
        base["base_url"] = settings.ollama_base_url or "http://localhost:11434/v1"
    return base


register_provider("openai", OpenAIProvider)

try:
    from .anthropic_provider import AnthropicProvider

    register_provider("anthropic", AnthropicProvider)
except ImportError:
    pass

try:
    from .gemini_provider import GeminiProvider

    register_provider("gemini", GeminiProvider)
except ImportError:
    pass

try:
    from .ollama_provider import OllamaProvider

    register_provider("ollama", OllamaProvider)
except ImportError:
    pass
