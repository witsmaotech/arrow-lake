"""OpenTelemetry telemetry setup for Arrow Lake.

Configures distributed tracing with OTLP export. Gracefully no-ops when
opentelemetry dependencies are not installed (optional dep group).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_OTEL_AVAILABLE = False

try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.sampling import TraceIdRatioBased

    _OTEL_AVAILABLE = True
except ImportError:
    pass


def _configure_tracer(config: Any) -> None:
    """Configure OTel tracer provider with OTLP exporter."""
    if not _OTEL_AVAILABLE:
        return

    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

    resource = Resource.create({SERVICE_NAME: config.service_name})

    sampler = TraceIdRatioBased(config.trace_sample_rate)
    provider = TracerProvider(resource=resource, sampler=sampler)

    exporter = OTLPSpanExporter(endpoint=config.otel_endpoint)
    provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(provider)
    logger.info(
        "OpenTelemetry configured: service=%s, endpoint=%s, sample_rate=%.2f",
        config.service_name,
        config.otel_endpoint,
        config.trace_sample_rate,
    )


def _instrument_app(app: Any) -> None:
    """Instrument a FastAPI app with OpenTelemetry."""
    if not _OTEL_AVAILABLE:
        return

    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor().instrument_app(app)
        logger.info("FastAPI instrumentation enabled")
    except ImportError as exc:
        logger.warning("Failed to instrument FastAPI: %s", exc)


def setup_telemetry(config: Any, *, app: Any = None) -> None:
    """Initialize OpenTelemetry tracing.

    Args:
        config: OpenTelemetryConfig instance with enabled, service_name,
                otel_endpoint, trace_sample_rate.
        app: Optional FastAPI app to instrument.

    Returns:
        None. No-ops when telemetry is disabled or deps not installed.
    """
    if not config.enabled:
        return

    if not _OTEL_AVAILABLE:
        logger.info(
            "OpenTelemetry requested but dependencies not installed. "
            "Install with: uv pip install opentelemetry-api opentelemetry-sdk "
            "opentelemetry-exporter-otlp-proto-grpc"
        )
        return

    _configure_tracer(config)

    if app is not None:
        _instrument_app(app)


def get_tracer(name: str = "arrow-lake"):
    """Get an OpenTelemetry tracer, or a no-op tracer if OTel is unavailable.

    Safe to call at module level or inside hot paths. The no-op tracer returns
    spans whose context-manager methods are no-ops.

    Args:
        name: Instrumentation library name.

    Returns:
        A tracer object with ``start_as_current_span(name)``.
    """
    if not _OTEL_AVAILABLE:
        return _NoOpTracer()
    from opentelemetry import trace

    return trace.get_tracer(name)


class _NoOpTracer:
    """No-op tracer returned when opentelemetry is not installed."""

    class _NoOpSpan:
        def __enter__(self) -> _NoOpTracer._NoOpSpan:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def set_attribute(self, key: str, value: Any) -> None:
            pass

        def add_event(self, name: str, **kwargs: Any) -> None:
            pass

        def end(self) -> None:
            pass

        def record_exception(self, exception: BaseException) -> None:
            pass

        def set_status(self, status: Any) -> None:
            pass

        @property
        def is_recording(self) -> bool:
            return False

    def start_as_current_span(self, name: str, **kwargs: Any) -> _NoOpTracer._NoOpSpan:
        return self._NoOpSpan()
