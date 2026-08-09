from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from opentelemetry.instrumentation.asgi import (
    collect_custom_headers_attributes,
    collect_request_attributes,
)
from opentelemetry.util.http import (
    SanitizeValue,
    normalise_request_header_name,
    normalise_response_header_name,
)

from app.core.observability import telemetry


class _RecordingSpan:
    def __init__(self, attributes: dict[str, Any]) -> None:
        self.attributes = attributes

    def is_recording(self) -> bool:
        return True

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value


def test_query_capability_is_removed_from_http_trace_attributes() -> None:
    secret = "scoped-document-capability"
    scope: dict[str, Any] = {
        "type": "http",
        "scheme": "https",
        "server": ("api.example.test", 443),
        "root_path": "",
        "path": "/api/eurooffice/file/material-id",
        "query_string": f"token={secret}&mode=view".encode(),
        "method": "GET",
        "http_version": "1.1",
        "headers": [],
    }
    attributes = collect_request_attributes(scope)
    assert secret in str(attributes)

    span = _RecordingSpan(attributes)
    telemetry._redact_query_credentials(span, scope)  # type: ignore[arg-type]

    assert secret not in str(span.attributes)
    assert span.attributes["http.url"] == (
        "https://api.example.test/api/eurooffice/file/material-id"
    )
    assert span.attributes["url.query"] == "[REDACTED]"


def test_capture_all_otel_headers_still_redacts_credentials() -> None:
    sanitizer = SanitizeValue(telemetry._SENSITIVE_HTTP_HEADERS)
    request_attributes = collect_custom_headers_attributes(
        {
            "headers": [
                (b"authorization", b"Bearer access-secret"),
                (b"cookie", b"browser_read=read-secret"),
                (b"x-oo-file-token", b"document-secret"),
                (b"x-request-id", b"safe-id"),
            ]
        },
        sanitizer,
        [".*"],
        normalise_request_header_name,
    )
    response_attributes = collect_custom_headers_attributes(
        {"headers": [(b"set-cookie", b"refresh_token=refresh-secret" )]},
        sanitizer,
        [".*"],
        normalise_response_header_name,
    )

    combined = str({**request_attributes, **response_attributes})
    assert "access-secret" not in combined
    assert "read-secret" not in combined
    assert "document-secret" not in combined
    assert "refresh-secret" not in combined
    assert request_attributes["http.request.header.x_request_id"] == ["safe-id"]


def test_instrument_fastapi_installs_the_query_redaction_hook(monkeypatch: Any) -> None:
    monkeypatch.setattr(telemetry.settings, "otel_endpoint", "https://otel.example.test")

    with patch.object(telemetry.FastAPIInstrumentor, "instrument_app") as instrument:
        app = MagicMock()
        telemetry.instrument_fastapi(app)

    instrument.assert_called_once_with(
        app,
        server_request_hook=telemetry._redact_query_credentials,
        http_capture_headers_sanitize_fields=[
            "authorization",
            "cookie",
            "proxy-authorization",
            "set-cookie",
            "x-oo-file-token",
        ],
    )


def test_setup_telemetry_initializes_provider_without_reinstrumenting_app(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(telemetry.settings, "otel_endpoint", "https://otel.example.test")
    monkeypatch.setattr(telemetry, "_tracer", None)
    provider = MagicMock()
    tracer = MagicMock()

    with (
        patch.object(telemetry, "TracerProvider", return_value=provider),
        patch.object(telemetry, "OTLPSpanExporter", return_value=MagicMock()),
        patch.object(telemetry, "BatchSpanProcessor", return_value=MagicMock()),
        patch.object(telemetry.trace, "set_tracer_provider"),
        patch.object(telemetry.trace, "get_tracer", return_value=tracer),
        patch.object(telemetry.FastAPIInstrumentor, "instrument_app") as instrument,
        patch.object(telemetry.RedisInstrumentor, "instrument") as redis_instrument,
    ):
        telemetry.setup_telemetry()

    instrument.assert_not_called()
    redis_instrument.assert_called_once_with()
