import logging
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI
from opentelemetry import propagate, trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.config import settings

logger = logging.getLogger(__name__)

__all__ = [
    "setup_telemetry",
    "shutdown_telemetry",
    "get_tracer",
    "inject_trace_context",
    "extract_trace_context",
]

_tracer: trace.Tracer | None = None


def setup_telemetry(app: FastAPI | None = None) -> None:
    """Initialise OpenTelemetry. No-op when otel_endpoint is empty."""
    if not settings.otel_endpoint:
        return
    resource = Resource.create({"service.name": "lectern-api"})
    provider = TracerProvider(resource=resource)
    insecure = settings.otel_insecure or settings.otel_endpoint.startswith("http://")
    exporter = OTLPSpanExporter(endpoint=settings.otel_endpoint, insecure=insecure)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    # Instrument FastAPI if web application instance is provided
    if app is not None:
        FastAPIInstrumentor.instrument_app(app)

    # Instrument Redis
    RedisInstrumentor().instrument()

    global _tracer
    _tracer = trace.get_tracer("lectern")
    logger.info("OpenTelemetry initialised — endpoint=%s", settings.otel_endpoint)


def shutdown_telemetry() -> None:
    """Flush pending spans and cleanly shut down OpenTelemetry."""
    if not settings.otel_endpoint:
        return
    try:
        provider = trace.get_tracer_provider()
        if isinstance(provider, TracerProvider):
            provider.shutdown()
            logger.info("OpenTelemetry trace provider shut down cleanly.")
    except Exception as exc:
        logger.warning("Error during OpenTelemetry shutdown: %s", exc)


def get_tracer() -> trace.Tracer:
    return _tracer or trace.get_tracer("lectern")


def inject_trace_context() -> dict[str, str]:
    """Return W3C traceparent/tracestate for ARQ job propagation."""
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier


def extract_trace_context(carrier: Mapping[str, Any]) -> Context:
    """Extract and return an OTel context from an ARQ job kwargs dict."""
    return propagate.extract(carrier)
