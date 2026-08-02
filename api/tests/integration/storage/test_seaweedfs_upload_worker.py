from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from app.core.storage import facade
from app.workers.upload.exceptions import UploadError
from app.workers.upload.stages.download import run_download_and_validate

pytestmark = pytest.mark.integration

_MIB = 1024 * 1024
_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2nJ0AAAAASUVORK5CYII="
)


@pytest.mark.asyncio
async def test_upload_worker_downloads_validates_and_hashes_real_object(
    tmp_path: Path,
    storage_key: Any,
) -> None:
    key = storage_key("worker/pixel.png")
    destination = tmp_path / "downloaded.png"
    destination.touch()
    expected_hash = hashlib.sha256(_ONE_PIXEL_PNG).hexdigest()
    await facade.upload_file(_ONE_PIXEL_PNG, key, content_type="image/png")

    result = await run_download_and_validate(
        destination,
        key,
        "pixel.png",
        "image/png",
        expected_hash,
        "seaweedfs-worker-test",
    )
    try:
        assert result.original_sha256 == expected_hash
        assert result.initial_size == len(_ONE_PIXEL_PNG)
        assert result.actual_mime == "image/png"
        assert result.pf.read_bytes() == _ONE_PIXEL_PNG
    finally:
        result.pf.cleanup()


@pytest.mark.asyncio
async def test_upload_worker_rechecks_limit_after_authoritative_mime_detection(
    tmp_path: Path,
    storage_key: Any,
) -> None:
    key = storage_key("worker/oversized.svg")
    payload = b"<svg xmlns='http://www.w3.org/2000/svg'>" + b"A" * (6 * _MIB) + b"</svg>"
    destination = tmp_path / "oversized.svg"
    destination.touch()
    await facade.upload_file(payload, key, content_type="image/png")

    with (
        patch(
            "app.workers.upload.stages.download.guess_mime_from_file_path",
            return_value="image/svg+xml",
        ),
        pytest.raises(UploadError, match="detected type image/svg\\+xml"),
    ):
        await run_download_and_validate(
            destination,
            key,
            "oversized.svg",
            "image/png",
            hashlib.sha256(payload).hexdigest(),
            "seaweedfs-worker-limit-test",
        )

    destination.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_upload_worker_rejects_storage_hash_mismatch(
    tmp_path: Path,
    storage_key: Any,
) -> None:
    key = storage_key("worker/hash-mismatch.png")
    destination = tmp_path / "hash-mismatch.png"
    destination.touch()
    await facade.upload_file(_ONE_PIXEL_PNG, key, content_type="image/png")

    with pytest.raises(UploadError, match="SHA-256 integrity check failed"):
        await run_download_and_validate(
            destination,
            key,
            "hash-mismatch.png",
            "image/png",
            "0" * 64,
            "seaweedfs-worker-hash-test",
        )

    destination.unlink(missing_ok=True)
