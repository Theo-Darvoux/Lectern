from __future__ import annotations

from typing import Any
from unittest.mock import patch

import httpx
import pytest

from app.core.storage import facade
from app.core.storage.s3 import MULTIPART_THRESHOLD

pytestmark = pytest.mark.integration


async def _put(url: str, payload: bytes) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
        return await client.put(url, content=payload)


async def _assert_rejected_put(
    url: str,
    payload: bytes,
    *,
    key: str,
    upload_id: str,
    seaweedfs_config: Any,
    seaweedfs_client_factory: Any,
) -> None:
    # SeaweedFS may reject a signed-length mismatch either with an HTTP error
    # response or by closing the connection while discarding the request body.
    try:
        response = await _put(url, payload)
    except httpx.TransportError:
        pass
    else:
        assert response.status_code >= 400

    # A connection reset is only accepted as rejection after the authoritative
    # multipart listing proves that SeaweedFS did not retain the invalid part.
    async with seaweedfs_client_factory() as client:
        listed = await client.list_parts(
            Bucket=seaweedfs_config.bucket,
            Key=key,
            UploadId=upload_id,
        )
    assert listed.get("Parts", []) == []


@pytest.mark.asyncio
async def test_presigned_part_binds_exact_non_final_length(
    storage_key: Any,
    seaweedfs_config: Any,
    seaweedfs_client_factory: Any,
) -> None:
    key = storage_key("signed-size-non-final.bin")
    upload_id = await facade.create_multipart_upload(key)
    expected = MULTIPART_THRESHOLD
    try:
        url = await facade.generate_presigned_upload_part(
            key,
            upload_id,
            1,
            ttl=120,
            content_length=expected,
        )
        await _assert_rejected_put(
            url,
            b"x" * (expected + 1),
            key=key,
            upload_id=upload_id,
            seaweedfs_config=seaweedfs_config,
            seaweedfs_client_factory=seaweedfs_client_factory,
        )
    finally:
        await facade.abort_multipart_upload(key, upload_id)


@pytest.mark.asyncio
async def test_presigned_part_rejects_undersized_non_final_body(
    storage_key: Any,
    seaweedfs_config: Any,
    seaweedfs_client_factory: Any,
) -> None:
    key = storage_key("signed-size-undersized-non-final.bin")
    upload_id = await facade.create_multipart_upload(key)
    expected = 8 * 1024 * 1024
    valid_s3_part = 5 * 1024 * 1024
    try:
        url = await facade.generate_presigned_upload_part(
            key,
            upload_id,
            1,
            ttl=120,
            content_length=expected,
        )
        await _assert_rejected_put(
            url,
            b"x" * valid_s3_part,
            key=key,
            upload_id=upload_id,
            seaweedfs_config=seaweedfs_config,
            seaweedfs_client_factory=seaweedfs_client_factory,
        )
    finally:
        await facade.abort_multipart_upload(key, upload_id)


@pytest.mark.asyncio
async def test_presigned_part_binds_exact_final_length(
    storage_key: Any,
    seaweedfs_config: Any,
    seaweedfs_client_factory: Any,
) -> None:
    key = storage_key("signed-size-final.bin")
    upload_id = await facade.create_multipart_upload(key)
    expected = 1024 * 1024 + 17
    try:
        url = await facade.generate_presigned_upload_part(
            key,
            upload_id,
            1,
            ttl=120,
            content_length=expected,
        )
        await _assert_rejected_put(
            url,
            b"x" * (expected - 1),
            key=key,
            upload_id=upload_id,
            seaweedfs_config=seaweedfs_config,
            seaweedfs_client_factory=seaweedfs_client_factory,
        )
    finally:
        await facade.abort_multipart_upload(key, upload_id)


@pytest.mark.asyncio
async def test_presigned_part_accepts_exact_signed_length(storage_key: Any) -> None:
    key = storage_key("signed-size-exact.bin")
    upload_id = await facade.create_multipart_upload(key)
    expected = MULTIPART_THRESHOLD
    try:
        url = await facade.generate_presigned_upload_part(
            key,
            upload_id,
            1,
            ttl=120,
            content_length=expected,
        )
        response = await _put(url, b"x" * expected)
        assert response.status_code in {200, 204}
        assert response.headers.get("etag")
    finally:
        await facade.abort_multipart_upload(key, upload_id)


@pytest.mark.asyncio
async def test_verified_completion_deletes_authoritative_size_mismatch(storage_key: Any) -> None:
    from app.core.storage.multipart_completion import (
        MultipartCompletionError,
        complete_multipart_verified,
    )

    key = storage_key("verified-size-mismatch.bin")
    upload_id = await facade.create_multipart_upload(key)
    payload = b"x" * MULTIPART_THRESHOLD
    etag = await facade.upload_part(key, upload_id, 1, payload)

    with pytest.raises(MultipartCompletionError) as raised:
        await complete_multipart_verified(
            key,
            upload_id,
            [{"PartNumber": 1, "ETag": etag}],
            expected_size=len(payload) - 1,
        )

    assert raised.value.retryable is False
    assert await facade.object_exists(key) is False


@pytest.mark.asyncio
async def test_verified_completion_recovers_after_lost_success_response(storage_key: Any) -> None:
    from app.core.storage.multipart_completion import complete_multipart_verified

    key = storage_key("verified-ambiguous-recovery.bin")
    upload_id = await facade.create_multipart_upload(key)
    payload = b"x" * MULTIPART_THRESHOLD
    etag = await facade.upload_part(key, upload_id, 1, payload)
    manifest = [{"PartNumber": 1, "ETag": etag}]
    real_complete = facade.complete_multipart_upload

    async def commit_then_drop_response(
        file_key: str,
        s3_upload_id: str,
        parts: list[dict[str, int | str]],
    ) -> None:
        await real_complete(file_key, s3_upload_id, parts)
        raise OSError("simulated lost completion response")

    with patch(
        "app.core.storage.multipart_completion.complete_multipart_upload",
        side_effect=commit_then_drop_response,
    ):
        result = await complete_multipart_verified(
            key,
            upload_id,
            manifest,
            expected_size=len(payload),
        )

    assert result.size == len(payload)
    assert result.recovered_after_error is True
    assert await facade.read_full_object(key) == payload
