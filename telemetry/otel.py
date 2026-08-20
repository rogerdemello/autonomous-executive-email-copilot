"""OpenTelemetry integration for the Executive Email Copilot.

Provides:
- ``configure_otel()`` — one-time setup of TracerProvider, MeterProvider, OTLP exporter
- ``tracer`` — module-level tracer for creating spans
- ``meter`` — module-level meter for creating instruments
- ``in_span()`` — context manager / decorator for instrumenting code paths

Gracefully degrades when ``opentelemetry-sdk`` is not installed: all calls become
no-ops and the legacy PrometheusMetrics path continues to work (dual-write).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

_OTEL_AVAILABLE = False
_tracer = None
_meter = None

try:
    from opentelemetry import trace as _trace_api
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider as _TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    _OTEL_AVAILABLE = True
except ImportError:
    logger.info("OpenTelemetry SDK not installed. Install optional group: pip install '.[otel]'")


def configure_otel(
    service_name: str = "exec-email-copilot",
    otlp_endpoint: str | None = None,
    enable_console: bool = False,
) -> None:
    """One-time setup of OpenTelemetry tracing.

    Args:
        service_name: Service name for resource attributes.
        otlp_endpoint: OTLP HTTP endpoint (e.g., ``http://tempo:4318/v1/traces``).
            Falls back to ``OTEL_EXPORTER_OTLP_ENDPOINT`` env var.
        enable_console: Also export spans to console (stderr) for debugging.
    """
    if not _OTEL_AVAILABLE:
        logger.warning("Cannot configure OTEL: opentelemetry-sdk not installed")
        return

    global _tracer, _meter

    resource = Resource.create(
        attributes={
            "service.name": service_name,
            "service.version": "1.0.0",
        }
    )

    provider = _TracerProvider(resource=resource)
    endpoint = otlp_endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")

    if endpoint:
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces"))
        )

    if enable_console or os.environ.get("OTEL_CONSOLE_ENABLED", "").lower() in ("1", "true"):
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    _trace_api.set_tracer_provider(provider)
    _tracer = _trace_api.get_tracer(__name__)

    logger.info("OpenTelemetry configured (endpoint=%s, console=%s)", endpoint, enable_console)


def get_tracer():
    """Get the module-level tracer, creating a no-op one if OTEL is not configured."""
    if _tracer is not None:
        return _tracer
    if _OTEL_AVAILABLE:
        from opentelemetry import trace as _trace_api

        return _trace_api.get_tracer(__name__)
    return _NoOpTracer()


def get_meter():
    """Get the module-level meter, creating a no-op one if OTEL is not configured."""
    if _meter is not None:
        return _meter
    return _NoOpMeter()


@contextmanager
def in_span(
    name: str,
    attributes: dict[str, Any] | None = None,
    kind: Any = None,
) -> Generator[Any, None, None]:
    """Context manager that creates an OTEL span.

    Usage::

        with in_span("llm_call", attributes={"model": "gpt-4o"}):
            response = provider.generate(...)

    Falls back to a no-op context manager when OTEL is not installed.
    """
    tr = get_tracer()
    with tr.start_as_current_span(name, kind=kind, attributes=attributes or {}) as span:
        yield span


class _NoOpTracer:
    """No-op tracer used when OpenTelemetry is not installed."""

    def start_span(self, name, **kwargs):
        return _NoOpSpan()

    def start_as_current_span(self, name, **kwargs):
        return _NoOpSpanContext()

    @contextmanager
    def start_as_current_span_cm(self, name, **kwargs):
        yield _NoOpSpan()


class _NoOpSpan:
    """No-op span."""

    def set_attribute(self, key, value):
        pass

    def add_event(self, name, attributes=None):
        pass

    def set_status(self, status):
        pass

    def end(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class _NoOpSpanContext:
    """No-op context manager for start_as_current_span."""

    def __enter__(self):
        return _NoOpSpan()

    def __exit__(self, *args):
        pass


class _NoOpMeter:
    """No-op meter used when OpenTelemetry is not installed."""

    def create_counter(self, name, **kwargs):
        return _NoOpInstrument()

    def create_histogram(self, name, **kwargs):
        return _NoOpInstrument()

    def create_gauge(self, name, **kwargs):
        return _NoOpInstrument()


class _NoOpInstrument:
    """No-op instrument."""

    def add(self, amount, attributes=None):
        pass

    def record(self, value, attributes=None):
        pass

    def set(self, value, attributes=None):
        pass
