from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from app.core.storage import facade
from app.core.storage.s3 import MULTIPART_THRESHOLD

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_content_type_rewrite_streams_same_key_without_local_spool(
    seaweedfs_backend: Any,
    storage_key: Any,
) -> None:
    key = storage_key("metadata-streaming-rewrite.bin")
    # More than one default multipart part, while remaining small enough for CI.
    seed = b"metadata-rewrite"
    target_size = 2 * MULTIPART_THRESHOLD + 123
    payload = (seed * ((target_size + len(seed) - 1) // len(seed)))[:target_size]

    await facade.upload_file(
        payload,
        key,
        content_type="application/octet-stream",
        content_encoding="gzip",
        content_disposition='inline; filename="metadata.bin"',
    )

    # The previous implementation always called download_file_raw() and wrote a
    # full temporary copy. The hardened implementation streams GET -> multipart
    # upload directly, so any regression to disk spooling makes this live test fail.
    with patch.object(
        seaweedfs_backend,
        "download_file_raw",
        side_effect=AssertionError("metadata rewrite must not spool the whole object to disk"),
    ):
        await facade.update_object_content_type(key, "application/x-regression")

    assert await facade.read_full_object(key) == payload
    headers = await facade.get_object_headers(key)
    assert headers["content_type"] == "application/x-regression"
    assert headers["content_encoding"] == "gzip"
    assert headers["content_disposition"] == 'inline; filename="metadata.bin"'
    assert headers["cache_control"] == "public, max-age=86400"
    assert [item async for item in facade.list_multipart_uploads(key)] == []
