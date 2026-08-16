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


def _detect_provider(settings) -> LLMProvider:

    explicit = (settings.llm_provider or "").strip().lower()
    if explicit and explicit in _provider_registry:
        kwargs = _resolve_provider_kwargs(explicit, settings)
        return _provider_registry[explicit](**kwargs)

    if settings.anthropic_api_key:
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=settings.model_name,
            timeout_seconds=30.0,
        )

    if settings.google_api_key:
        from .gemini_provider import GeminiProvider

        return GeminiProvider(
            api_key=settings.google_api_key,
            model=settings.model_name,
            timeout_seconds=30.0,
        )

    if settings.azure_openai_endpoint and settings.azure_openai_deployment_name:
        return _build_openai_provider(settings)

    if settings.openai_api_key:
        return _build_openai_provider(settings)

    if settings.ollama_base_url:
        from .ollama_provider import OllamaProvider

        return OllamaProvider(
            base_url=settings.ollama_base_url,
            model=settings.model_name,
            timeout_seconds=30.0,
        )

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
    return CircuitBreakingProvider(primary=provider, breaker=breaker)


def _resolve_provider_kwargs(name: str, settings) -> dict:
    base = {
        "model": settings.model_name,
        "temperature": 0.2,
        "timeout_seconds": 30.0,
    }
    if name == "openai":
        base["model"] = settings.model_name
    elif name == "anthropic":
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
