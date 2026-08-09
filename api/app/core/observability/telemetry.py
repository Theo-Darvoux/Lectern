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
    "instrument_fastapi",
    "setup_telemetry",
    "shutdown_telemetry",
    "get_tracer",
    "inject_trace_context",
    "extract_trace_context",
]

_tracer: trace.Tracer | None = None
_SENSITIVE_HTTP_HEADERS = [
    "authorization",
    "cookie",
    "proxy-authorization",
    "set-cookie",
    "x-oo-file-token",
]


def _redact_query_credentials(span: trace.Span, scope: dict[str, Any]) -> None:
    """Remove query values from HTTP span attributes.

    OpenTelemetry's ASGI instrumentation includes the decoded query string in
    ``http.url``/``url.full`` by default. This application has unavoidable
    short-lived capability query parameters for third-party document delivery,
    so traces must retain only the request path.
    """
    if not scope.get("query_string") or not span.is_recording():
        return

    path = f"{scope.get('root_path', '')}{scope.get('path', '')}" or "/"
    scheme = str(scope.get("scheme") or "http")
    server = scope.get("server")
    if isinstance(server, (tuple, list)) and server:
        host = str(server[0])
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = server[1] if len(server) > 1 else None
        default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
        authority = host if port is None or default_port else f"{host}:{port}"
        safe_url = f"{scheme}://{authority}{path}"
    else:
        safe_url = path

    # Set both legacy and stable semantic-convention names. Setting an existing
    # attribute replaces the value recorded by the ASGI instrumentation.
    span.set_attribute("http.url", safe_url)
    span.set_attribute("http.target", path)
    span.set_attribute("url.full", safe_url)
    span.set_attribute("url.path", path)
    span.set_attribute("url.query", "[REDACTED]")


def instrument_fastapi(app: FastAPI) -> None:
    """Wrap the application before its middleware stack is built.

    This deliberately does not create exporters or worker threads: production
    Gunicorn preloads the module before forking workers.
    """
    if not settings.otel_endpoint:
        return
    FastAPIInstrumentor.instrument_app(
        app,
        server_request_hook=_redact_query_credentials,
        # This applies even when an operator enables capture-all headers via
        # OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_{REQUEST,RESPONSE}.
        http_capture_headers_sanitize_fields=_SENSITIVE_HTTP_HEADERS,
    )


def setup_telemetry() -> None:
    """Initialise the per-process OpenTelemetry provider and exporters."""
    if not settings.otel_endpoint:
        return
    resource = Resource.create({"service.name": "lectern-api"})
    provider = TracerProvider(resource=resource)
    insecure = settings.otel_insecure or settings.otel_endpoint.startswith("http://")
    exporter = OTLPSpanExporter(endpoint=settings.otel_endpoint, insecure=insecure)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

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
