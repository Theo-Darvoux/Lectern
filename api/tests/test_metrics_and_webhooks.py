"""Tests for Phase 7: Prometheus metrics and webhook dispatch."""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.observability.metrics import (
    mime_category,
)
from app.models.upload import Upload

# ── mime_category helper ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "mime,expected",
    [
        ("image/png", "image"),
        ("image/svg+xml", "image"),
        ("video/mp4", "video"),
        ("video/webm", "video"),
        ("audio/mpeg", "audio"),
        ("audio/flac", "audio"),
        ("text/plain", "text"),
        ("text/csv", "text"),
        ("application/pdf", "document"),
        ("application/epub+zip", "document"),
        ("image/vnd.djvu", "document"),
        ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "office"),
        ("application/msword", "office"),
        ("application/vnd.ms-excel", "office"),
        ("application/octet-stream", "other"),
        ("application/zip", "other"),
    ],
)
def test_mime_category(mime: str, expected: str) -> None:
    assert mime_category(mime) == expected


# ── /metrics endpoint ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_prometheus_text(client) -> None:
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "lectern_upload_pipeline_total" in response.text


@pytest.mark.asyncio
async def test_metrics_endpoint_token_required(client) -> None:
    """When METRICS_TOKEN is configured, unauthenticated requests get 403."""
    from app.config import settings

    original = settings.metrics_token
    try:
        settings.metrics_token = "secret-scrape-token"
        resp = await client.get("/metrics")
        assert resp.status_code == 403
    finally:
        settings.metrics_token = original


@pytest.mark.asyncio
async def test_metrics_endpoint_token_accepted_via_header(client) -> None:
    from app.config import settings

    original = settings.metrics_token
    try:
        settings.metrics_token = "secret-scrape-token"
        resp = await client.get(
            "/metrics",
            headers={"Authorization": "Bearer secret-scrape-token"},
        )
        assert resp.status_code == 200
    finally:
        settings.metrics_token = original


@pytest.mark.asyncio
async def test_metrics_endpoint_token_rejected_via_query(client) -> None:
    from app.config import settings

    original = settings.metrics_token
    try:
        settings.metrics_token = "secret-scrape-token"
        resp = await client.get("/metrics?token=secret-scrape-token")
        assert resp.status_code == 403
    finally:
        settings.metrics_token = original


# ── dispatch_webhook job ──────────────────────────────────────────────────────


def _make_upload(webhook_url: str | None = "https://example.com/hook") -> Upload:
    return Upload(
        upload_id=str(uuid.uuid4()),
        user_id=uuid.uuid4(),
        quarantine_key="quarantine/user/id/file.pdf",
        final_key="uploads/user/id/file.pdf",
        status="clean",
        cas_ref_count=1,
        sha256="abc123",
        webhook_url=webhook_url,
        filename="file.pdf",
        mime_type="application/pdf",
        size_bytes=12345,
    )


def _make_ctx(upload: Upload) -> dict:
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=upload)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    session_factory = MagicMock(return_value=session)
    return {"db_sessionmaker": session_factory}


@pytest.fixture(autouse=True)
def mock_is_safe_url():
    from app.core.security.url_validation import ResolvedHttpsUrl

    target = ResolvedHttpsUrl("https://example.com/hook", "example.com", ("93.184.216.34",))
    with patch(
        "app.workers.webhook_dispatch.resolve_safe_url_async",
        new_callable=AsyncMock,
        return_value=target,
    ):
        yield


@pytest.mark.asyncio
async def test_webhook_dispatched_successfully() -> None:
    from app.workers.webhook_dispatch import dispatch_webhook

    upload = _make_upload()
    ctx = _make_ctx(upload)

    with patch(
        "app.workers.webhook_dispatch.post_pinned_https", new_callable=AsyncMock
    ) as mock_post:
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        await dispatch_webhook(ctx, upload_id=upload.upload_id)

    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    assert call_kwargs[0][0].url == "https://example.com/hook"
    headers = call_kwargs[1]["headers"]
    assert "X-Lectern-Signature" in headers
    assert headers["X-Lectern-Signature"].startswith("sha256=")
    assert "X-Lectern-Delivery" in headers
    assert headers["X-Lectern-Event"] == "upload.complete"


@pytest.mark.asyncio
async def test_webhook_payload_structure() -> None:
    from app.workers.webhook_dispatch import dispatch_webhook

    upload = _make_upload()
    ctx = _make_ctx(upload)

    captured_body: bytes | None = None

    async def fake_post(url, *, content, headers, **kwargs):
        nonlocal captured_body
        captured_body = content
        r = MagicMock()
        r.is_success = True
        r.status_code = 200
        return r

    with patch("app.workers.webhook_dispatch.post_pinned_https", side_effect=fake_post):
        await dispatch_webhook(ctx, upload_id=upload.upload_id)

    assert captured_body is not None
    payload = json.loads(captured_body)
    assert payload["event"] == "upload.complete"
    assert payload["upload_id"] == upload.upload_id
    assert payload["status"] == "clean"
    assert payload["file_key"] == "uploads/user/id/file.pdf"
    assert payload["sha256"] == "abc123"
    assert "timestamp" in payload


@pytest.mark.asyncio
async def test_webhook_signature_is_valid() -> None:
    from app.workers.webhook_dispatch import _sign, dispatch_webhook

    upload = _make_upload()
    ctx = _make_ctx(upload)

    received_sig: str | None = None
    received_body: bytes | None = None

    async def fake_post(url, *, content, headers, **kwargs):
        nonlocal received_sig, received_body
        received_sig = headers["X-Lectern-Signature"]
        received_body = content
        r = MagicMock()
        r.is_success = True
        r.status_code = 200
        return r

    with patch("app.workers.webhook_dispatch.post_pinned_https", side_effect=fake_post):
        await dispatch_webhook(ctx, upload_id=upload.upload_id)

    assert received_body is not None
    expected = _sign(received_body)
    assert received_sig == expected


@pytest.mark.asyncio
async def test_webhook_skipped_when_no_url() -> None:
    from app.workers.webhook_dispatch import dispatch_webhook

    upload = _make_upload(webhook_url=None)
    ctx = _make_ctx(upload)

    with patch(
        "app.workers.webhook_dispatch.post_pinned_https", new_callable=AsyncMock
    ) as mock_post:
        await dispatch_webhook(ctx, upload_id=upload.upload_id)

    mock_post.assert_not_called()


@pytest.mark.asyncio
async def test_webhook_skipped_when_no_session_factory() -> None:
    from app.workers.webhook_dispatch import dispatch_webhook

    with patch(
        "app.workers.webhook_dispatch.post_pinned_https", new_callable=AsyncMock
    ) as mock_post:
        await dispatch_webhook({}, upload_id="some-id")

    mock_post.assert_not_called()


@pytest.mark.asyncio
async def test_webhook_retries_on_5xx() -> None:
    from app.workers.webhook_dispatch import dispatch_webhook

    upload = _make_upload()
    ctx = _make_ctx(upload)

    async def fake_post(url, *, content, headers, **kwargs):
        # Transient error on first attempt
        r = MagicMock()
        r.is_success = False
        r.status_code = 503
        return r

    enqueue_called = []

    async def fake_enqueue(job_name, **kwargs):
        enqueue_called.append((job_name, kwargs))

    mock_arq = MagicMock()
    mock_arq.enqueue_job = fake_enqueue
    ctx["arq"] = mock_arq

    with patch("app.workers.webhook_dispatch.post_pinned_https", side_effect=fake_post):
        await dispatch_webhook(ctx, upload_id=upload.upload_id, attempt=1)

    # On transient error, should enqueue retry
    assert len(enqueue_called) == 1
    assert enqueue_called[0][0] == "dispatch_webhook"
    assert enqueue_called[0][1]["upload_id"] == upload.upload_id
    assert enqueue_called[0][1]["attempt"] == 2


@pytest.mark.asyncio
async def test_webhook_no_retry_on_4xx() -> None:
    from app.workers.webhook_dispatch import dispatch_webhook

    upload = _make_upload()
    ctx = _make_ctx(upload)

    call_count = 0

    async def fake_post(url, *, content, headers, **kwargs):
        nonlocal call_count
        call_count += 1
        r = MagicMock()
        r.is_success = False
        r.status_code = 404
        return r

    with patch("app.workers.webhook_dispatch.post_pinned_https", side_effect=fake_post):
        await dispatch_webhook(ctx, upload_id=upload.upload_id)

    # 4xx is a permanent error — only 1 attempt
    assert call_count == 1


@pytest.mark.asyncio
async def test_webhook_no_raise_on_network_error() -> None:
    from app.workers.webhook_dispatch import dispatch_webhook

    upload = _make_upload()
    ctx = _make_ctx(upload)

    from app.core.security.url_validation import PinnedRequestError

    with patch(
        "app.workers.webhook_dispatch.post_pinned_https",
        side_effect=PinnedRequestError("refused"),
    ):
        with patch("asyncio.sleep", new_callable=AsyncMock):
            # Must not raise even after all retries exhausted
            await dispatch_webhook(ctx, upload_id=upload.upload_id)


@pytest.mark.asyncio
async def test_webhook_credentials_never_reach_logs(caplog: pytest.LogCaptureFixture) -> None:
    from app.core.security.url_validation import PinnedRequestError
    from app.workers.webhook_dispatch import dispatch_webhook

    url_secret = "callback-query-secret"
    transport_secret = "transport-error-secret"
    upload = _make_upload(webhook_url=f"https://example.com/hook?key={url_secret}")
    ctx = _make_ctx(upload)

    with patch(
        "app.workers.webhook_dispatch.resolve_safe_url_async",
        new_callable=AsyncMock,
        return_value=None,
    ):
        await dispatch_webhook(ctx, upload_id=upload.upload_id)

    with patch(
        "app.workers.webhook_dispatch.post_pinned_https",
        side_effect=PinnedRequestError(
            f"request failed for https://example.com/hook?key={transport_secret}"
        ),
    ):
        await dispatch_webhook(ctx, upload_id=upload.upload_id)

    assert url_secret not in caplog.text
    assert transport_secret not in caplog.text


@pytest.mark.asyncio
async def test_webhook_skips_cancelled_upload() -> None:
    from app.workers.webhook_dispatch import dispatch_webhook

    upload = _make_upload()
    upload.status = "cancelled"
    upload.cas_ref_count = 0
    ctx = _make_ctx(upload)

    with patch(
        "app.workers.webhook_dispatch.post_pinned_https", new_callable=AsyncMock
    ) as mock_post:
        await dispatch_webhook(ctx, upload_id=upload.upload_id)

    mock_post.assert_not_awaited()


@pytest.mark.asyncio
async def test_webhook_applied_upload_uses_clean_snapshot_status() -> None:
    from app.workers.webhook_dispatch import dispatch_webhook

    upload = _make_upload()
    upload.status = "applied"
    upload.cas_ref_count = 0
    ctx = _make_ctx(upload)
    captured_body: bytes | None = None

    async def fake_post(url, *, content, headers, **kwargs):
        nonlocal captured_body
        captured_body = content
        response = MagicMock()
        response.is_success = True
        response.status_code = 200
        return response

    with patch("app.workers.webhook_dispatch.post_pinned_https", side_effect=fake_post):
        await dispatch_webhook(ctx, upload_id=upload.upload_id)

    assert captured_body is not None
    assert json.loads(captured_body)["status"] == "clean"
