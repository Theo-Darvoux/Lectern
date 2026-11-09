"""Waiting for a sanitizer slot must not consume the sanitizer's own time budget."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.core.security.file_security._concurrency import _get_concurrency_guard
from app.workers.upload.stages import scan_strip


@pytest.mark.asyncio
async def test_production_guard_uses_requested_acquire_timeout() -> None:
    semaphore = MagicMock()
    semaphore.return_value.__aenter__ = AsyncMock()
    semaphore.return_value.__aexit__ = AsyncMock(return_value=False)

    with (
        patch.object(settings, "environment", "production"),
        patch("app.core.security.file_security._concurrency.redis_semaphore", semaphore),
    ):
        async with _get_concurrency_guard("image", acquire_timeout=300.0):
            pass

    assert semaphore.call_args.kwargs["timeout"] == 300.0


@pytest.mark.asyncio
async def test_queued_sanitize_outlives_work_timeout(tmp_path: Path) -> None:
    source = tmp_path / "upload.pdf"
    source.write_bytes(b"%PDF-1.4")

    async def queued_sanitize(path: Path, **_: object) -> Path:
        await asyncio.sleep(0.2)
        return path

    sanitize = AsyncMock(side_effect=queued_sanitize)
    ctx = SimpleNamespace(scanner=MagicMock(scan_file_path=AsyncMock()))

    with (
        patch.object(scan_strip, "_SANITIZE_WORK_TIMEOUT_SECONDS", 0.05),
        patch.object(scan_strip, "_SANITIZE_QUEUE_TIMEOUT_SECONDS", 5),
        patch.object(scan_strip, "sanitize_upload", sanitize),
    ):
        await scan_strip.run_scan_and_strip(
            ctx,  # type: ignore[arg-type]
            MagicMock(replace_with=AsyncMock()),
            source,
            "upload.pdf",
            "sha",
            "application/pdf",
            "document",
            "upload-id",
            MagicMock(),
        )

    assert sanitize.call_args.kwargs["queue_timeout"] == 5
