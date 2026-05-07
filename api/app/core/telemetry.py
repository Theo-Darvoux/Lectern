import logging
from typing import Any

from opentelemetry import propagate, trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.config import settings

logger = logging.getLogger("wikint")

_tracer: trace.Tracer | None = None


def setup_telemetry(app: Any) -> None:
    """Initialise OpenTelemetry. No-op when otel_endpoint is empty."""
    if not settings.otel_endpoint:
        return
    resource = Resource.create({"service.name": "wikint-api"})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=settings.otel_endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    # Instrument FastAPI
    FastAPIInstrumentor.instrument_app(app)

    # Instrument Redis
    RedisInstrumentor().instrument()

    global _tracer
    _tracer = trace.get_tracer("wikint")
    logger.info("OpenTelemetry initialised — endpoint=%s", settings.otel_endpoint)


def get_tracer() -> trace.Tracer:
    return _tracer or trace.get_tracer("wikint")


def inject_trace_context() -> dict:  # type: ignore[type-arg]
    """Return W3C traceparent/tracestate for ARQ job propagation."""
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier


def extract_trace_context(carrier: dict) -> Any:  # type: ignore[type-arg]
    """Extract and return an OTel context from an ARQ job kwargs dict."""
    return propagate.extract(carrier)
