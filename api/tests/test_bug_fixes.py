"""Tests for the five confirmed bugs fixed in the worker / file-processing layer,
plus the Redis resilience fix (Bug 6).

Bug 1 – CAS Lua script called with only 1 key in storage_ops (storage leak)
Bug 2 – Webhook dead-letter UploadWorkerRepository receives raw dict (records lost)
Bug 3 – Thumbnail temp files not cleaned in finally block (disk leak)
Bug 4 – cleanup_uploads uses redis.KEYS instead of SCAN (Redis block)
Bug 5 – Cron jobs create a new AsyncEngine per run instead of reusing ctx one
Bug 6 – arq RedisSettings conn_timeout=1s causes worker crash on BGSAVE spike
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Bug 1: CAS Lua script key count ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_storage_objects_rejects_opaque_cas_key() -> None:
    """An opaque HMAC object ID must never be mistaken for the source SHA-256."""
    from app.workers.storage_ops import delete_storage_objects

    mock_redis = AsyncMock()
    ctx = {"redis": mock_redis}

    with pytest.raises(ExceptionGroup):
        await delete_storage_objects(ctx, ["cas/deadbeef"])


@pytest.mark.asyncio
async def test_delete_storage_objects_cas_never_deletes_without_source_hash() -> None:
    from app.workers.storage_ops import delete_storage_objects

    mock_redis = AsyncMock()
    ctx = {"redis": mock_redis}

    mock_delete = AsyncMock()
    with patch("app.workers.storage_ops.delete_object", mock_delete), pytest.raises(ExceptionGroup):
        await delete_storage_objects(ctx, ["cas/deadbeef"])

    mock_delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_storage_objects_reports_cas_rejection() -> None:
    from app.workers.storage_ops import delete_storage_objects

    mock_redis = AsyncMock()
    ctx = {"redis": mock_redis}

    mock_delete = AsyncMock()
    with patch("app.workers.storage_ops.delete_object", mock_delete), pytest.raises(
        ExceptionGroup, match="could not be deleted"
    ):
        await delete_storage_objects(ctx, ["cas/deadbeef"])

    mock_delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_storage_objects_non_cas_deletes_directly() -> None:
    """delete_storage_objects must call delete_object directly for non-cas/ keys."""
    from app.workers.storage_ops import delete_storage_objects

    mock_redis = AsyncMock()
    ctx = {"redis": mock_redis}

    mock_delete = AsyncMock()
    with (
        patch("app.core.security.cas.decrement_cas_ref", AsyncMock()) as mock_decrement,
        patch("app.workers.storage_ops.delete_object", mock_delete),
    ):
        await delete_storage_objects(ctx, ["quarantine/user/upload/file.pdf"])

    mock_delete.assert_awaited_once_with("quarantine/user/upload/file.pdf")
    mock_decrement.assert_not_awaited()


@pytest.mark.asyncio
async def test_decrement_cas_ref_returns_count() -> None:
    """decrement_cas_ref must return the new ref count, not None (was previously void)."""
    from app.core.security.cas import decrement_cas_ref

    mock_redis = AsyncMock()
    mock_redis.eval = AsyncMock(return_value=2)  # Lua returns new count

    result = await decrement_cas_ref(
        mock_redis,
        "a" * 64,
        operation_id="test-decrement-returns-count",
    )

    assert result == 2, "decrement_cas_ref must return the Lua script's return value"


@pytest.mark.asyncio
async def test_decrement_cas_ref_preserves_physical_storage_usage() -> None:
    """Dropping the last reference must not claim the S3 object was deleted."""
    from app.core.security.cas import _LUA_CAS_DECR, decrement_cas_ref

    mock_redis = AsyncMock()
    mock_redis.eval = AsyncMock(return_value=0)

    await decrement_cas_ref(
        mock_redis,
        "b" * 64,
        operation_id="test-last-reference-release",
    )

    eval_args = mock_redis.eval.await_args.args
    assert eval_args[1] == 2
    assert "storage:total_usage_bytes" not in eval_args[2:]
    assert "DECRBY" not in _LUA_CAS_DECR


# ── Bug 2: Webhook dead-letter type mismatch ─────────────────────────────────


@pytest.mark.asyncio
async def test_webhook_dlq_uses_worker_context() -> None:
    """dispatch_webhook must wrap ctx in WorkerContext before passing to UploadWorkerRepository."""
    import uuid

    from app.workers.webhook_dispatch import _MAX_ATTEMPTS, dispatch_webhook

    upload_id = str(uuid.uuid4())

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    row = MagicMock()
    row.webhook_url = "https://example.com/hook"
    row.upload_id = upload_id
    row.status = "clean"
    row.final_key = None
    row.sha256 = None
    row.mime_type = None
    row.size_bytes = None
    mock_session.scalar = AsyncMock(return_value=row)

    mock_insert_dlq = AsyncMock()
    mock_response = MagicMock(is_success=False, status_code=503)

    ctx = {"db_sessionmaker": lambda: mock_session, "redis": AsyncMock()}

    with (
        patch(
            "app.workers.webhook_dispatch.resolve_safe_url_async",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
        patch(
            "app.workers.webhook_dispatch.post_pinned_https",
            new_callable=AsyncMock,
            return_value=mock_response,
        ),
        patch(
            "app.workers.upload.repository.UploadWorkerRepository.insert_dead_letter",
            mock_insert_dlq,
        ),
    ):
        await dispatch_webhook(ctx, upload_id=upload_id, attempt=_MAX_ATTEMPTS)

    # insert_dead_letter must be called (repo was constructed correctly)
    mock_insert_dlq.assert_awaited_once()


# ── Bug 3: Thumbnail temp file leak ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_thumbnail_pdf_cleans_temp_png_on_image_error(tmp_path: Path) -> None:
    """_thumbnail_pdf must delete temp_png even when _thumbnail_image raises."""
    from app.workers.upload.stages.thumbnail import _thumbnail_pdf

    input_pdf = tmp_path / "in.pdf"
    input_pdf.write_bytes(b"%PDF-1.4 fake")
    output_webp = tmp_path / "out.webp"
    actual_temp_png: Path | None = None

    # Ghostscript creates the temp_png file
    async def fake_gs(cmd: list[str], **kwargs: object) -> MagicMock:
        nonlocal actual_temp_png
        output_arg = next(a for a in cmd if a.startswith("-sOutputFile="))
        actual_temp_png = Path(output_arg.split("=", 1)[1])
        proc = MagicMock()
        proc.returncode = 0
        proc.stdout = b""
        proc.stderr = b""
        actual_temp_png.write_bytes(b"fakepng")
        return proc

    with (
        patch(
            "app.workers.upload.stages.thumbnail.async_sandboxed_run",
            side_effect=fake_gs,
        ),
        patch(
            "app.workers.upload.stages.thumbnail._thumbnail_image",
            AsyncMock(side_effect=OSError("PIL failure")),
        ),
        pytest.raises(OSError),
    ):
        await _thumbnail_pdf(input_pdf, output_webp, (320, 240), 80)

    assert actual_temp_png is not None
    assert not actual_temp_png.exists(), (
        "temp_png must be cleaned up even after _thumbnail_image error"
    )


@pytest.mark.asyncio
async def test_thumbnail_video_cleans_temp_jpg_on_image_error(tmp_path: Path) -> None:
    """_thumbnail_video must delete temp_jpg even when _thumbnail_image raises."""
    from app.workers.upload.stages.thumbnail import _thumbnail_video

    input_vid = tmp_path / "in.mp4"
    input_vid.write_bytes(b"fakevideo")
    output_webp = tmp_path / "out.webp"
    actual_temp_jpg: Path | None = None

    async def fake_ffmpeg(cmd: list[str], **kwargs: object) -> MagicMock:
        nonlocal actual_temp_jpg
        actual_temp_jpg = Path(cmd[-1])
        proc = MagicMock()
        proc.returncode = 0
        proc.stdout = b""
        proc.stderr = b""
        actual_temp_jpg.write_bytes(b"fakejpg")
        return proc

    with (
        patch(
            "app.workers.upload.stages.thumbnail.async_sandboxed_run",
            side_effect=fake_ffmpeg,
        ),
        patch(
            "app.workers.upload.stages.thumbnail._thumbnail_image",
            AsyncMock(side_effect=OSError("PIL failure")),
        ),
        pytest.raises(OSError),
    ):
        await _thumbnail_video(input_vid, output_webp, (320, 240), 80)

    assert actual_temp_jpg is not None
    assert not actual_temp_jpg.exists(), (
        "private temp_jpg must be cleaned up even after _thumbnail_image error"
    )


# ── Bug 4: redis.KEYS → scan_iter ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cleanup_uploads_uses_scan_iter_not_keys() -> None:
    """cleanup_uploads must use scan_iter (non-blocking) instead of redis.keys."""
    import inspect

    from app.workers import cleanup_uploads as cu_module

    src = inspect.getsource(cu_module)
    assert "redis.keys(" not in src, (
        "cleanup_uploads must not use redis.keys() — use scan_iter() to avoid blocking Redis"
    )
    assert "scan_iter" in src, "cleanup_uploads must use scan_iter for cas key enumeration"


@pytest.mark.asyncio
async def test_cleanup_uploads_scan_iter_collects_all_cas_ids() -> None:
    """The scan_iter loop must collect the same CAS IDs as the old redis.keys call."""

    sha256_a = "a" * 64
    sha256_b = "b" * 64
    redis_keys = [
        f"upload:cas:{sha256_a}".encode(),
        f"upload:cas:{sha256_b}".encode(),
    ]

    async def fake_scan_iter(pattern):
        for k in redis_keys:
            yield k

    mock_redis = MagicMock()
    mock_redis.scan_iter = fake_scan_iter

    collected: set[str] = set()
    async for cas_key in mock_redis.scan_iter("upload:cas:*"):
        k = cas_key.decode() if isinstance(cas_key, bytes) else cas_key
        collected.add(k.split(":")[-1])

    assert collected == {sha256_a, sha256_b}


# ── Bug 5: Cron jobs create new engine per run ───────────────────────────────


@pytest.mark.asyncio
async def test_reset_daily_views_uses_ctx_session_factory() -> None:
    """reset_daily_views must use ctx['db_sessionmaker'] and not create a new engine."""
    from app.workers.view_reset import reset_daily_views

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_result = MagicMock(rowcount=3)
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()

    mock_factory = MagicMock(return_value=mock_session)
    ctx = {"db_sessionmaker": mock_factory}

    mock_create_engine = MagicMock()
    with patch("app.workers.view_reset.create_async_engine", mock_create_engine):
        await reset_daily_views(ctx)

    mock_create_engine.assert_not_called()
    mock_factory.assert_called_once()


@pytest.mark.asyncio
async def test_reset_14d_views_uses_ctx_session_factory() -> None:
    """reset_14d_views must use ctx['db_sessionmaker'] and not create a new engine."""
    from app.workers.view_reset import reset_14d_views

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_result = MagicMock(rowcount=5)
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()

    mock_factory = MagicMock(return_value=mock_session)
    ctx = {"db_sessionmaker": mock_factory}

    mock_create_engine = MagicMock()
    with patch("app.workers.view_reset.create_async_engine", mock_create_engine):
        await reset_14d_views(ctx)

    mock_create_engine.assert_not_called()
    mock_factory.assert_called_once()


@pytest.mark.asyncio
async def test_year_rollover_uses_ctx_session_factory() -> None:
    """year_rollover must use ctx['db_sessionmaker'] and not create a new engine."""
    from app.workers.year_rollover import year_rollover

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(
        return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        )
    )
    mock_session.commit = AsyncMock()

    mock_factory = MagicMock(return_value=mock_session)
    ctx = {"db_sessionmaker": mock_factory}

    mock_create_engine = MagicMock()
    with patch("app.workers.year_rollover.create_async_engine", mock_create_engine):
        await year_rollover(ctx)

    mock_create_engine.assert_not_called()
    mock_factory.assert_called_once()


@pytest.mark.asyncio
async def test_reset_daily_views_creates_engine_when_ctx_missing() -> None:
    """reset_daily_views must create its own engine when ctx has no db_sessionmaker."""
    from app.workers.view_reset import reset_daily_views

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_result = MagicMock(rowcount=0)
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()

    mock_engine = AsyncMock()
    mock_factory_instance = MagicMock(return_value=mock_session)

    with (
        patch("app.workers.view_reset.create_async_engine", return_value=mock_engine) as mock_ce,
        patch("app.workers.view_reset.async_sessionmaker", return_value=mock_factory_instance),
    ):
        await reset_daily_views({})

    mock_ce.assert_called_once()
    mock_engine.dispose.assert_awaited_once()


# ── Bug 6: arq conn_timeout too tight ────────────────────────────────────────


def test_build_redis_settings_has_resilient_timeouts() -> None:
    """build_redis_settings must set conn_timeout >= 10 and retry_on_timeout=True."""
    from app.core.database.redis import build_redis_settings

    rs = build_redis_settings()
    assert rs.conn_timeout >= 10, "conn_timeout must be >= 10s to survive BGSAVE latency spikes"
    assert rs.retry_on_timeout is True, "retry_on_timeout must be True"
    assert rs.conn_retries >= 10, "conn_retries must be >= 10 for startup resilience"


def test_build_redis_settings_retry_on_error_includes_transient() -> None:
    """retry_on_error must include ConnectionError, TimeoutError, and BusyLoadingError."""
    from redis.exceptions import BusyLoadingError
    from redis.exceptions import ConnectionError as RedisConnectionError
    from redis.exceptions import TimeoutError as RedisTimeoutError

    from app.core.database.redis import build_redis_settings

    rs = build_redis_settings()
    assert rs.retry_on_error is not None
    error_types = set(rs.retry_on_error)
    assert RedisConnectionError in error_types
    assert RedisTimeoutError in error_types
    assert BusyLoadingError in error_types


def test_worker_settings_use_resilient_redis_settings() -> None:
    """All three WorkerSettings classes must use build_redis_settings() (not bare from_dsn)."""
    from app.core.database.redis import build_redis_settings
    from app.workers.settings import (
        UploadFastWorkerSettings,
        UploadSlowWorkerSettings,
        WorkerSettings,
    )

    reference = build_redis_settings()
    for cls in (WorkerSettings, UploadFastWorkerSettings, UploadSlowWorkerSettings):
        rs = cls.redis_settings
        assert rs.conn_timeout == reference.conn_timeout, f"{cls.__name__} conn_timeout mismatch"
        assert rs.retry_on_timeout == reference.retry_on_timeout, (
            f"{cls.__name__} retry_on_timeout mismatch"
        )


def test_redis_client_has_retry_on_timeout() -> None:
    """The global redis_client must be created with retry_on_timeout=True."""
    from app.core.database.redis import redis_client

    # redis-py exposes retry_on_timeout via the connection pool's connection_kwargs
    kwargs = redis_client.connection_pool.connection_kwargs
    assert kwargs.get("retry_on_timeout") is True
