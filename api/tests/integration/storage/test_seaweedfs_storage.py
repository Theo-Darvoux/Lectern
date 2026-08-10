from __future__ import annotations

import asyncio
import gzip
import hashlib
import io
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from botocore.exceptions import ClientError

from app.core.security.cas import hmac_cas_key
from app.core.storage import facade
from app.core.storage.s3 import MULTIPART_THRESHOLD

pytestmark = pytest.mark.integration

_MIB = 1024 * 1024


def _payload(size: int) -> bytes:
    seed = bytes(range(251))
    repeats, remainder = divmod(size, len(seed))
    return seed * repeats + seed[:remainder]


async def _stream_all(body: Any, chunk_size: int = 97_531) -> bytes:
    chunks: list[bytes] = []
    while chunk := await body.read(chunk_size):
        chunks.append(chunk)
    return b"".join(chunks)


@pytest.mark.asyncio
async def test_backend_lifecycle_and_persistent_client(
    seaweedfs_backend: Any,
    storage_key: Any,
) -> None:
    key = storage_key("lifecycle.bin")
    await seaweedfs_backend.init_s3_client()
    try:
        assert seaweedfs_backend._s3 is not None
        await facade.upload_file(b"persistent-client", key)
        assert await facade.read_full_object(key) == b"persistent-client"
    finally:
        await seaweedfs_backend.close_s3_client()

    assert seaweedfs_backend._s3 is None


@pytest.mark.asyncio
async def test_bytes_round_trip_metadata_and_info(storage_key: Any) -> None:
    key = storage_key("metadata.txt")
    payload = b"SeaweedFS integration round trip"

    await facade.upload_file(
        payload,
        key,
        content_type="text/plain",
        content_disposition='inline; filename="metadata.txt"',
    )

    assert await facade.read_full_object(key) == payload
    assert await facade.object_exists(key)

    info = await facade.get_object_info(key)
    assert info == {"size": len(payload), "content_type": "text/plain"}

    headers = await facade.get_object_headers(key)
    assert headers["content_type"] == "text/plain"
    assert headers["content_disposition"] == 'inline; filename="metadata.txt"'
    assert headers["cache_control"] == "public, max-age=86400"


@pytest.mark.asyncio
async def test_direct_client_context_and_cas_existence(
    seaweedfs_config: Any,
    storage_key: Any,
) -> None:
    async with facade.get_s3_client() as client:
        response = await client.head_bucket(Bucket=seaweedfs_config.bucket)
    assert response["ResponseMetadata"]["HTTPStatusCode"] == 200

    payload = b"content-addressed-object"
    sha256 = hashlib.sha256(payload).hexdigest()
    cas_id = hmac_cas_key(sha256).split(":")[-1]
    await facade.upload_file(payload, f"cas/{cas_id}")

    assert await facade.cas_object_exists(sha256)
    assert not await facade.cas_object_exists("0" * 64)


@pytest.mark.asyncio
async def test_file_like_empty_and_unicode_keys(storage_key: Any) -> None:
    file_key = storage_key("dossier/été notes + space.txt")
    empty_key = storage_key("empty.bin")

    await facade.upload_file(io.BytesIO(b"file-like"), file_key, content_type="text/plain")
    await facade.upload_file(b"", empty_key, content_type="application/octet-stream")

    assert await facade.read_full_object(file_key) == b"file-like"
    assert await facade.read_object_bytes(file_key, 4) == b"file"
    assert await facade.read_full_object(empty_key) == b""
    assert await facade.read_object_bytes(empty_key, 32) == b""
    assert await facade.read_object_bytes(storage_key("missing.bin"), 32) == b""


@pytest.mark.asyncio
async def test_download_raw_hash_and_expected_size(tmp_path: Path, storage_key: Any) -> None:
    key = storage_key("download.bin")
    payload = _payload(512 * 1024 + 17)
    expected_hash = hashlib.sha256(payload).hexdigest()
    destination = tmp_path / "download.bin"

    await facade.upload_file(payload, key)
    digest = await facade.download_file_with_hash(
        key,
        destination,
        max_bytes=len(payload),
        expected_size=len(payload),
    )

    assert digest == expected_hash
    assert destination.read_bytes() == payload

    with pytest.raises(ValueError, match="size changed during download"):
        await facade.download_file_with_hash(
            key,
            tmp_path / "wrong-size.bin",
            expected_size=len(payload) + 1,
        )


@pytest.mark.asyncio
async def test_download_limit_fails_closed(tmp_path: Path, storage_key: Any) -> None:
    key = storage_key("over-limit.bin")
    await facade.upload_file(_payload(256 * 1024), key)

    with pytest.raises(ValueError, match="exceeds download size limit"):
        await facade.download_file_raw(
            key,
            tmp_path / "limited.bin",
            max_bytes=64 * 1024,
        )


@pytest.mark.asyncio
async def test_read_full_object_enforces_memory_bound(storage_key: Any) -> None:
    key = storage_key("read-full-bound.bin")
    await facade.upload_file(_payload(4097), key)

    with (
        patch("app.core.storage.s3._READ_FULL_OBJECT_MAX_BYTES", 4096),
        pytest.raises(ValueError, match="read_full_object"),
    ):
        await facade.read_full_object(key)


@pytest.mark.asyncio
async def test_gzip_raw_and_decompressed_downloads(tmp_path: Path, storage_key: Any) -> None:
    key = storage_key("compressed.json")
    original = b'{"message":"' + b"A" * 250_000 + b'"}'
    compressed = gzip.compress(original)

    await facade.upload_file(
        compressed,
        key,
        content_type="application/json",
        content_encoding="gzip",
    )

    raw_path = tmp_path / "raw.gz"
    decoded_path = tmp_path / "decoded.json"
    await facade.download_file_raw(key, raw_path)
    await facade.download_file(key, decoded_path, decompress=True)

    assert raw_path.read_bytes() == compressed
    assert decoded_path.read_bytes() == original


@pytest.mark.asyncio
async def test_streaming_body_reads_all_bytes(storage_key: Any) -> None:
    key = storage_key("stream.bin")
    payload = _payload(2 * _MIB + 123)
    await facade.upload_file(payload, key)

    async with facade.stream_object(key) as body:
        streamed = await _stream_all(body)

    assert streamed == payload


@pytest.mark.asyncio
async def test_update_content_type_preserves_transport_headers(storage_key: Any) -> None:
    key = storage_key("headers.bin")
    await facade.upload_file(
        b"header-preservation",
        key,
        content_type="application/octet-stream",
        content_encoding="gzip",
        content_disposition='inline; filename="old.bin"',
    )

    await facade.update_object_content_type(key, "application/x-custom")
    headers = await facade.get_object_headers(key)

    assert await facade.read_full_object(key) == b"header-preservation"
    assert headers["content_type"] == "application/x-custom"
    assert headers["content_encoding"] == "gzip"
    assert headers["content_disposition"] == 'inline; filename="old.bin"'
    assert headers["cache_control"] == "public, max-age=86400"


@pytest.mark.asyncio
async def test_copy_move_delete_and_existence(storage_key: Any) -> None:
    source = storage_key("source.bin")
    copied = storage_key("copied.bin")
    moved = storage_key("moved.bin")
    payload = b"copy-and-move"

    await facade.upload_file(payload, source)
    await facade.copy_object(source, copied)
    assert await facade.read_full_object(source) == payload
    assert await facade.read_full_object(copied) == payload

    await facade.move_object(copied, moved)
    assert not await facade.object_exists(copied)
    assert await facade.read_full_object(moved) == payload

    await facade.delete_object(moved)
    await facade.delete_object(moved)
    assert not await facade.object_exists(moved)


@pytest.mark.asyncio
async def test_prefix_listing_is_complete_and_isolated(storage_key: Any) -> None:
    expected = {storage_key(f"listed/{index:02d}.bin") for index in range(32)}
    unrelated = storage_key("unrelated.bin")

    await asyncio.gather(
        *(facade.upload_file(str(index).encode(), key) for index, key in enumerate(expected)),
        facade.upload_file(b"unrelated", unrelated),
    )

    prefix = next(iter(expected)).rsplit("/", 1)[0] + "/"
    observed = {str(item["Key"]) async for item in facade.list_objects(prefix)}

    assert observed == expected
    assert unrelated not in observed


@pytest.mark.asyncio
async def test_concurrent_put_get_and_overwrite(storage_key: Any) -> None:
    objects = {
        storage_key(f"concurrent/{index}.bin"): _payload(64 * 1024 + index) for index in range(16)
    }
    await asyncio.gather(*(facade.upload_file(value, key) for key, value in objects.items()))
    downloaded = await asyncio.gather(*(facade.read_full_object(key) for key in objects))
    assert downloaded == list(objects.values())

    overwrite_key = storage_key("overwrite.bin")
    await facade.upload_file(b"first", overwrite_key)
    await facade.upload_file(b"second", overwrite_key)
    assert await facade.read_full_object(overwrite_key) == b"second"


@pytest.mark.asyncio
async def test_manual_multipart_round_trip_and_listing(storage_key: Any) -> None:
    key = storage_key("manual-multipart.bin")
    part_one = _payload(MULTIPART_THRESHOLD)
    part_two = _payload(_MIB + 37)

    upload_id = await facade.create_multipart_upload(
        key,
        content_type="application/octet-stream",
    )
    listed = [item async for item in facade.list_multipart_uploads(key)]
    assert any(item["UploadId"] == upload_id and item["Key"] == key for item in listed)

    etag_one, etag_two = await asyncio.gather(
        facade.upload_part(key, upload_id, 1, part_one),
        facade.upload_part(key, upload_id, 2, part_two),
    )
    await facade.complete_multipart_upload(
        key,
        upload_id,
        [
            {"PartNumber": 1, "ETag": etag_one},
            {"PartNumber": 2, "ETag": etag_two},
        ],
    )

    assert await facade.read_full_object(key) == part_one + part_two
    assert [item async for item in facade.list_multipart_uploads(key)] == []


@pytest.mark.asyncio
async def test_abort_multipart_is_idempotent(storage_key: Any) -> None:
    key = storage_key("aborted.bin")
    upload_id = await facade.create_multipart_upload(key)
    await facade.upload_part(key, upload_id, 1, _payload(MULTIPART_THRESHOLD))

    await facade.abort_multipart_upload(key, upload_id)
    await facade.abort_multipart_upload(key, upload_id)

    assert not await facade.object_exists(key)
    assert [item async for item in facade.list_multipart_uploads(key)] == []


@pytest.mark.asyncio
async def test_multipart_helper_falls_back_for_small_file(
    tmp_path: Path,
    storage_key: Any,
) -> None:
    key = storage_key("small-helper.bin")
    source = tmp_path / "small-helper.bin"
    payload = _payload(256 * 1024)
    source.write_bytes(payload)

    await facade.upload_file_multipart(
        source,
        key,
        chunk_size=MULTIPART_THRESHOLD,
    )

    assert await facade.read_full_object(key) == payload
    assert [item async for item in facade.list_multipart_uploads(key)] == []


@pytest.mark.asyncio
async def test_multipart_file_helper_round_trip(tmp_path: Path, storage_key: Any) -> None:
    key = storage_key("helper-multipart.bin")
    source = tmp_path / "source.bin"
    destination = tmp_path / "destination.bin"

    hasher = hashlib.sha256()
    with source.open("wb") as file_obj:
        for index in range(18):
            block = hashlib.sha256(f"block-{index}".encode()).digest() * (32 * 1024)
            file_obj.write(block)
            hasher.update(block)

    await facade.upload_file_multipart(
        source,
        key,
        chunk_size=MULTIPART_THRESHOLD,
    )
    observed_hash = await facade.download_file_with_hash(
        key,
        destination,
        expected_size=source.stat().st_size,
    )

    assert observed_hash == hasher.hexdigest()
    assert destination.read_bytes() == source.read_bytes()


@pytest.mark.asyncio
async def test_multipart_helper_aborts_remote_upload_after_part_failure(
    tmp_path: Path,
    storage_key: Any,
    seaweedfs_backend: Any,
) -> None:
    key = storage_key("failed-multipart.bin")
    source = tmp_path / "failed-source.bin"
    source.write_bytes(_payload(11 * _MIB))
    original_upload_part = seaweedfs_backend.upload_part

    async def fail_second_part(
        file_key: str,
        upload_id: str,
        part_number: int,
        body: bytes,
    ) -> str:
        if part_number == 2:
            raise RuntimeError("injected multipart failure")
        return await original_upload_part(file_key, upload_id, part_number, body)

    with (
        patch.object(seaweedfs_backend, "upload_part", side_effect=fail_second_part),
        pytest.raises(RuntimeError, match="injected multipart failure"),
    ):
        await facade.upload_file_multipart(
            source,
            key,
            chunk_size=MULTIPART_THRESHOLD,
        )

    assert not await facade.object_exists(key)
    assert [item async for item in facade.list_multipart_uploads(key)] == []


@pytest.mark.asyncio
async def test_multipart_helper_rejects_small_part_size(
    tmp_path: Path,
    storage_key: Any,
) -> None:
    source = tmp_path / "small.bin"
    source.write_bytes(_payload(MULTIPART_THRESHOLD + 1))

    with pytest.raises(ValueError, match="at least 5 MiB"):
        await facade.upload_file_multipart(
            source,
            storage_key("invalid-parts.bin"),
            chunk_size=MULTIPART_THRESHOLD - 1,
        )


@pytest.mark.asyncio
async def test_storage_enforces_expiry_for_cas_mutation_capabilities(
    seaweedfs_backend: Any,
    storage_key: Any,
) -> None:
    """Expired PUT/COPY/DELETE capabilities must be rejected by SeaweedFS itself."""
    suffix = uuid.uuid4().hex
    source_key = storage_key("capability source.bin")
    source_payload = b"copy-source-payload"
    await seaweedfs_backend.upload_file(source_payload, source_key)

    fresh_put_key = f"cas/capability-fresh-put-{suffix}"
    fresh_copy_key = f"cas/capability-fresh-copy-{suffix}"
    fresh_delete_key = f"cas/capability-fresh-delete-{suffix}"

    fresh_put = await seaweedfs_backend.presign_cas_put_capability(
        fresh_put_key, ttl=30, content_length=9
    )
    fresh_copy = await seaweedfs_backend.presign_cas_copy_capability(
        source_key, fresh_copy_key, ttl=30
    )
    fresh_delete_seed = await seaweedfs_backend.presign_cas_put_capability(
        fresh_delete_key, ttl=30, content_length=9
    )
    fresh_delete = await seaweedfs_backend.presign_cas_delete_capability(fresh_delete_key, ttl=30)
    assert fresh_put.recovery_fence_ms >= 30_000
    assert fresh_copy.recovery_fence_ms >= 30_000
    assert fresh_delete.recovery_fence_ms >= 30_000
    await seaweedfs_backend.execute_presigned_mutation(fresh_put, body=b"fresh-put")
    await seaweedfs_backend.execute_presigned_mutation(fresh_delete_seed, body=b"delete-me")
    await seaweedfs_backend.execute_presigned_mutation(fresh_copy)
    await seaweedfs_backend.execute_presigned_mutation(fresh_delete)

    assert await seaweedfs_backend.read_full_object(fresh_put_key) == b"fresh-put"
    assert await seaweedfs_backend.read_full_object(fresh_copy_key) == source_payload
    assert not await seaweedfs_backend.object_exists(fresh_delete_key)

    expired_put_key = f"cas/capability-expired-put-{suffix}"
    expired_copy_key = f"cas/capability-expired-copy-{suffix}"
    expired_delete_key = f"cas/capability-expired-delete-{suffix}"
    expired_delete_seed = await seaweedfs_backend.presign_cas_put_capability(
        expired_delete_key, ttl=30, content_length=12
    )
    await seaweedfs_backend.execute_presigned_mutation(expired_delete_seed, body=b"must-survive")

    expired_put = await seaweedfs_backend.presign_cas_put_capability(
        expired_put_key, ttl=1, content_length=11
    )
    expired_copy = await seaweedfs_backend.presign_cas_copy_capability(
        source_key, expired_copy_key, ttl=1
    )
    expired_delete = await seaweedfs_backend.presign_cas_delete_capability(
        expired_delete_key, ttl=1
    )

    # Cross a full integer-second expiry boundary with margin; the request is
    # then rejected by the storage service, independently of application locks.
    await asyncio.sleep(2.1)

    for capability, body in (
        (expired_put, b"expired-put"),
        (expired_copy, None),
        (expired_delete, None),
    ):
        with pytest.raises(RuntimeError, match="rejected by the object store"):
            await seaweedfs_backend.execute_presigned_mutation(capability, body=body)

    assert not await seaweedfs_backend.object_exists(expired_put_key)
    assert not await seaweedfs_backend.object_exists(expired_copy_key)
    assert await seaweedfs_backend.read_full_object(expired_delete_key) == b"must-survive"


@pytest.mark.asyncio
async def test_cas_multipart_and_bidirectional_moves_use_capability_path(
    tmp_path: Path,
    storage_key: Any,
) -> None:
    """Exercise large-file PUT plus both move directions through real SeaweedFS."""
    suffix = uuid.uuid4().hex
    payload = _payload(MULTIPART_THRESHOLD + 257_123)
    source = tmp_path / "capability-large.bin"
    source.write_bytes(payload)
    cas_key = f"cas/capability-large-{suffix}"

    await facade.upload_file_multipart(
        source,
        cas_key,
        content_type="application/octet-stream",
        chunk_size=MULTIPART_THRESHOLD,
    )
    assert await facade.read_full_object(cas_key) == payload

    outside_key = storage_key("capability-move-out.bin")
    await facade.move_object(cas_key, outside_key)
    assert not await facade.object_exists(cas_key)
    assert await facade.read_full_object(outside_key) == payload

    returned_cas_key = f"cas/capability-move-back-{suffix}"
    await facade.move_object(outside_key, returned_cas_key)
    assert not await facade.object_exists(outside_key)
    assert await facade.read_full_object(returned_cas_key) == payload


@pytest.mark.asyncio
async def test_presigned_put_and_get_over_http(storage_key: Any) -> None:
    key = storage_key("presigned.bin")
    payload = _payload(128 * 1024 + 11)
    content_type = "application/octet-stream"

    put_url = await facade.generate_presigned_put(
        key,
        content_type,
        ttl=120,
        content_length=len(payload),
    )

    async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
        put_response = await client.put(
            put_url,
            content=payload,
            headers={"Content-Type": content_type},
        )
        assert put_response.status_code in {200, 204}

        get_url = await facade.generate_presigned_get(
            key,
            ttl=120,
            force_download=False,
        )
        get_response = await client.get(get_url)

    assert get_response.status_code == 200
    assert get_response.content == payload


@pytest.mark.asyncio
async def test_presigned_get_applies_download_response_overrides(storage_key: Any) -> None:
    key = storage_key("response-overrides.bin")
    await facade.upload_file(b"response-overrides", key)

    url = await facade.generate_presigned_get(
        key,
        ttl=120,
        force_download=True,
        filename="cours été.pdf",
        content_type="application/pdf",
    )
    async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
        response = await client.get(url)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "filename*=UTF-8''cours%20%C3%A9t%C3%A9.pdf" in disposition


@pytest.mark.asyncio
async def test_presigned_multipart_upload_over_http(storage_key: Any) -> None:
    key = storage_key("presigned-multipart.bin")
    first = _payload(MULTIPART_THRESHOLD)
    second = _payload(777_777)
    upload_id = await facade.create_multipart_upload(key)

    try:
        first_url, second_url = await asyncio.gather(
            facade.generate_presigned_upload_part(key, upload_id, 1, ttl=120),
            facade.generate_presigned_upload_part(key, upload_id, 2, ttl=120),
        )
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            first_response, second_response = await asyncio.gather(
                client.put(first_url, content=first),
                client.put(second_url, content=second),
            )

        assert first_response.status_code in {200, 204}
        assert second_response.status_code in {200, 204}
        await facade.complete_multipart_upload(
            key,
            upload_id,
            [
                {"PartNumber": 1, "ETag": first_response.headers["etag"]},
                {"PartNumber": 2, "ETag": second_response.headers["etag"]},
            ],
        )
    except Exception:
        await facade.abort_multipart_upload(key, upload_id)
        raise

    assert await facade.read_full_object(key) == first + second


@pytest.mark.asyncio
async def test_presigned_get_refuses_quarantine_keys(storage_key: Any) -> None:
    suffix = storage_key("unsafe.bin").split("/", 1)[1]
    with pytest.raises(ValueError, match="unscanned quarantine key"):
        await facade.generate_presigned_get(f"quarantine/{suffix}")


@pytest.mark.asyncio
async def test_invalid_credentials_are_rejected(
    seaweedfs_config: Any,
    seaweedfs_client_factory: Any,
) -> None:
    async with seaweedfs_client_factory(
        access_key="invalid-access-key",
        secret_key="invalid-secret-key",
    ) as client:
        with pytest.raises(ClientError) as exc_info:
            await client.list_objects_v2(Bucket=seaweedfs_config.bucket)

    assert exc_info.value.response["ResponseMetadata"]["HTTPStatusCode"] in {401, 403}


@pytest.mark.asyncio
async def test_public_url_uses_path_style_endpoint(
    seaweedfs_config: Any,
    storage_key: Any,
) -> None:
    key = storage_key("public path/été.txt")
    url = await facade.get_public_url(key)

    assert url.startswith(f"{seaweedfs_config.endpoint_url}/{seaweedfs_config.bucket}/")
    assert "public%20path/%C3%A9t%C3%A9.txt" in url
