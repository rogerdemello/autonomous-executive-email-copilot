from __future__ import annotations

import logging
import time
from enum import Enum
from typing import Any

from .base import LLMProvider, LLMResponse

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Per-(provider, model) circuit breaker for LLM calls.

    State machine: CLOSED (normal) → OPEN (after N failures) → HALF_OPEN
    (after timeout) → CLOSED (on success) or OPEN (on failure).
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
        half_open_max_retries: int = 1,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_retries = half_open_max_retries

        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0.0
        self.half_open_attempts = 0
        self.total_trips = 0

    @property
    def is_available(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time >= self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                self.half_open_attempts = 0
                logger.info(
                    "Circuit %s: OPEN → HALF_OPEN after %.1fs", self.name, self.recovery_timeout
                )
                return True
            return False
        if self.state == CircuitState.HALF_OPEN:
            return self.half_open_attempts < self.half_open_max_retries
        return False

    def on_success(self) -> None:
        if self.state == CircuitState.HALF_OPEN:
            logger.info("Circuit %s: HALF_OPEN → CLOSED (success)", self.name)
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.half_open_attempts = 0

    def on_failure(self) -> None:
        self.failure_count += 1
        if self.state == CircuitState.HALF_OPEN:
            self.half_open_attempts += 1
            if self.half_open_attempts >= self.half_open_max_retries:
                self.state = CircuitState.OPEN
                self.last_failure_time = time.time()
                self.total_trips += 1
                logger.warning(
                    "Circuit %s: HALF_OPEN → OPEN (failed %d/%d attempts)",
                    self.name,
                    self.half_open_attempts,
                    self.half_open_max_retries,
                )
        elif self.state == CircuitState.CLOSED:
            if self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN
                self.last_failure_time = time.time()
                self.total_trips += 1
                logger.warning(
                    "Circuit %s: CLOSED → OPEN (%d failures)",
                    self.name,
                    self.failure_count,
                )

    def metrics(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "total_trips": self.total_trips,
            "last_failure_time": self.last_failure_time,
        }


class AllProvidersFailedError(Exception):
    """All configured LLM providers are unavailable (circuits open or no providers)."""

    pass


class CircuitBreakingProvider(LLMProvider):
    """Wraps an LLMProvider with circuit breaker logic.

    Delegates to the primary provider. On repeated failures, opens the circuit
    and falls back to a secondary provider if available.
    """

    def __init__(
        self,
        primary: LLMProvider,
        secondary: LLMProvider | None = None,
        breaker: CircuitBreaker | None = None,
    ):
        self._primary = primary
        self._secondary = secondary
        self._breaker = breaker or CircuitBreaker(
            name=primary.provider_name,
            failure_threshold=3,
            recovery_timeout=30.0,
        )
        # Set here rather than exposed as a read-only property: `provider_name`
        # is a writeable attribute on LLMProvider, and a property cannot
        # override one. Same value, and it now agrees with the interface this
        # class claims to implement.
        self.provider_name = f"{primary.provider_name}+circuit"

    @property
    def capabilities(self) -> set[str]:
        return self._primary.capabilities

    def _get_active_provider(self) -> LLMProvider:
        if not self._breaker.is_available:
            if self._secondary is not None:
                logger.info(
                    "Circuit %s is OPEN, falling back to %s",
                    self._breaker.name,
                    self._secondary.provider_name,
                )
                return self._secondary
            raise AllProvidersFailedError(
                f"Circuit {self._breaker.name} is OPEN and no secondary provider"
            )
        return self._primary

    def generate(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        provider = self._get_active_provider()
        try:
            response = provider.generate(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
                tools=tools,
            )
            self._breaker.on_success()
            return response
        except AllProvidersFailedError:
            raise
        except Exception as e:
            self._breaker.on_failure()
            # If we're using the secondary and it fails, give up
            if provider is self._secondary:
                raise AllProvidersFailedError(
                    f"Secondary provider {self._secondary.provider_name} also failed: {e}"
                ) from e
            # Try secondary if available
            if self._secondary is not None:
                logger.info("Primary failed, trying secondary: %s", self._secondary.provider_name)
                try:
                    return self._secondary.generate(
                        messages=messages,
                        model=model,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        response_format=response_format,
                        tools=tools,
                    )
                except Exception as e2:
                    raise AllProvidersFailedError(
                        f"Primary and secondary both failed: {e}, {e2}"
                    ) from e2
            raise

    async def agenerate(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Async twin of :meth:`generate`, with the same breaker semantics.

        Must be defined here: the base class's ``agenerate`` default calls the
        *synchronous* ``generate()``, so without this override every async
        caller through the (always-on) circuit wrapper blocked the event loop
        and the wrapped provider's real async client was unreachable.
        """
        provider = self._get_active_provider()
        try:
            response = await provider.agenerate(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
                tools=tools,
            )
            self._breaker.on_success()
            return response
        except AllProvidersFailedError:
            raise
        except Exception as e:
            self._breaker.on_failure()
            if provider is self._secondary:
                raise AllProvidersFailedError(
                    f"Secondary provider {self._secondary.provider_name} also failed: {e}"
                ) from e
            if self._secondary is not None:
                logger.info("Primary failed, trying secondary: %s", self._secondary.provider_name)
                try:
                    return await self._secondary.agenerate(
                        messages=messages,
                        model=model,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        response_format=response_format,
                        tools=tools,
                    )
                except Exception as e2:
                    raise AllProvidersFailedError(
                        f"Primary and secondary both failed: {e}, {e2}"
                    ) from e2
            raise

    def generate_stream(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ):
        provider = self._get_active_provider()
        try:
            yield from provider.generate_stream(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
                tools=tools,
            )
            self._breaker.on_success()
        except AllProvidersFailedError:
            raise
        except Exception:
            self._breaker.on_failure()
            raise

    def get_metrics(self) -> dict[str, Any]:
        return {
            "breaker": self._breaker.metrics(),
            "primary": self._primary.provider_name,
            "secondary": self._secondary.provider_name if self._secondary else None,
        }
