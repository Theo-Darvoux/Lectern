"""Cancellation and error-precedence tests for S3 ownership cleanup."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("aioboto3")

from app.core.storage.s3 import MULTIPART_THRESHOLD, S3Backend


class _Body:
    def __init__(self, *, read_error: BaseException | None = None) -> None:
        self.read_error = read_error
        self.close_started = asyncio.Event()
        self.close_allowed = asyncio.Event()
        self.closed = False

    async def read(self, _size: int | None = None) -> bytes:
        if self.read_error is not None:
            raise self.read_error
        return b""

    async def close(self) -> None:
        self.close_started.set()
        await self.close_allowed.wait()
        self.closed = True


class _Client:
    def __init__(self, body: _Body) -> None:
        self.body = body

    async def get_object(self, **_kwargs: Any) -> dict[str, Any]:
        return {"Body": self.body}


class _DownloadBackend(S3Backend):
    def __init__(self, body: _Body) -> None:
        self.body = body

    def _cfg(self) -> dict[str, Any]:
        return {"bucket": "test"}

    @asynccontextmanager
    async def _client(self, _cfg: dict[str, Any] | None = None):
        yield _Client(self.body)


@pytest.mark.asyncio
async def test_download_primary_error_survives_repeated_cancel_during_close(
    tmp_path: Path,
) -> None:
    body = _Body(read_error=ValueError("primary"))
    backend = _DownloadBackend(body)
    task = asyncio.create_task(backend.download_file("key", tmp_path / "output"))

    await body.close_started.wait()
    task.cancel()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()

    body.close_allowed.set()
    with pytest.raises(ValueError, match="primary"):
        await task
    assert body.closed


@pytest.mark.asyncio
async def test_download_cancellation_waits_for_body_close(tmp_path: Path) -> None:
    body = _Body()
    backend = _DownloadBackend(body)
    task = asyncio.create_task(backend.download_file("key", tmp_path / "output"))

    await body.close_started.wait()
    task.cancel()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()

    body.close_allowed.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert body.closed


class _MultipartBackend(_DownloadBackend):
    def __init__(self) -> None:
        super().__init__(_Body())
        self.upload_started = asyncio.Event()
        self.abort_started = asyncio.Event()
        self.abort_allowed = asyncio.Event()
        self.aborted = False

    async def create_multipart_upload(self, *_args: Any, **_kwargs: Any) -> str:
        return "upload-id"

    async def upload_part(self, *_args: Any, **_kwargs: Any) -> str:
        self.upload_started.set()
        await asyncio.Event().wait()
        return "etag"

    async def abort_multipart_upload(self, *_args: Any, **_kwargs: Any) -> None:
        self.abort_started.set()
        await self.abort_allowed.wait()
        self.aborted = True

    async def complete_multipart_upload(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("cancelled multipart upload must not complete")


@pytest.mark.asyncio
async def test_multipart_repeated_cancellation_waits_for_abort(tmp_path: Path) -> None:
    backend = _MultipartBackend()
    source = tmp_path / "large.bin"
    source.write_bytes(b"x" * (MULTIPART_THRESHOLD + 1))

    task = asyncio.create_task(
        backend.upload_file_multipart(source, "key", chunk_size=MULTIPART_THRESHOLD)
    )
    await backend.upload_started.wait()
    task.cancel()
    await backend.abort_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()

    backend.abort_allowed.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert backend.aborted


@pytest.mark.asyncio
async def test_backend_rejects_every_permanent_credential_cas_mutation_escape_hatch(
    tmp_path: Path,
) -> None:
    """Canonical mutations may never reach a backend method with process-long credentials."""
    from app.core.storage.backends import SeaweedFSBackend

    backend = object.__new__(S3Backend)
    seaweed = object.__new__(SeaweedFSBackend)
    source = tmp_path / "cas-static-bypass.bin"
    source.write_bytes(b"payload")
    parts = [{"PartNumber": 1, "ETag": '"etag"'}]

    async def invoke(name: str) -> None:
        match name:
            case "create-multipart":
                await backend.create_multipart_upload("cas/x")
            case "upload-part":
                await backend.upload_part("cas/x", "upload-id", 1, b"x")
            case "complete-multipart":
                await backend.complete_multipart_upload("cas/x", "upload-id", parts)
            case "multipart-helper":
                await backend.upload_file_multipart(source, "cas/x")
            case "put":
                await backend.upload_file(b"x", "cas/x")
            case "presigned-part":
                await backend.generate_presigned_upload_part("cas/x", "upload-id", 1)
            case "generic-presigned-put":
                await backend.generate_presigned_put("cas/x", "application/octet-stream")
            case "metadata-rewrite":
                await backend.update_object_content_type("cas/x", "application/octet-stream")
            case "seaweed-metadata-rewrite":
                await seaweed.update_object_content_type("cas/x", "application/octet-stream")
            case "copy":
                await backend.copy_object("quarantine/source", "cas/x")
            case "move-from-cas":
                await backend.move_object("cas/x", "quarantine/dest")
            case "move-to-cas":
                await backend.move_object("quarantine/source", "cas/x")
            case "delete":
                await backend.delete_object("cas/x")
            case _:
                raise AssertionError(name)

    for operation in (
        "create-multipart",
        "upload-part",
        "complete-multipart",
        "multipart-helper",
        "put",
        "presigned-part",
        "generic-presigned-put",
        "metadata-rewrite",
        "seaweed-metadata-rewrite",
        "copy",
        "move-from-cas",
        "move-to-cas",
        "delete",
    ):
        with pytest.raises(
            RuntimeError,
            match="Canonical cas/ mutations require a pre-dispatch expiring storage capability",
        ):
            await invoke(operation)


class _CopyCapabilityClient:
    def __init__(self) -> None:
        self.operation: str | None = None
        self.params: dict[str, Any] | None = None
        self.expires_in: int | None = None

    async def generate_presigned_url(self, operation: str, **kwargs: Any) -> str:
        self.operation = operation
        self.params = kwargs["Params"]
        self.expires_in = kwargs["ExpiresIn"]
        return "https://storage.example/bucket/dest?X-Amz-Date=20260810T000000Z&X-Amz-Expires=30"


class _CopyCapabilityBackend(S3Backend):
    def __init__(self) -> None:
        self.client = _CopyCapabilityClient()

    def _cfg(self) -> dict[str, Any]:
        return {"bucket": "test-bucket"}

    @asynccontextmanager
    async def _client(self, _cfg: dict[str, Any] | None = None):
        yield self.client

    async def _presigned_recovery_fence_ms(
        self,
        _client: Any,
        *,
        bucket: str,
        url: str,
        requested_ttl: int,
    ) -> int:
        assert bucket == "test-bucket"
        assert "X-Amz-Expires=30" in url
        assert requested_ttl == 30
        return 30_000


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_key", "expected_header"),
    (
        ("folder/capability source.bin", "test-bucket/folder/capability%20source.bin"),
        (
            "folder/already%2Fencoded+question?.bin",
            "test-bucket/folder/already%252Fencoded%2Bquestion%3F.bin",
        ),
        ("folder/caf\u00e9.bin", "test-bucket/folder/caf%C3%A9.bin"),
    ),
)
async def test_copy_capability_signs_raw_source_and_sends_exactly_once_encoded_header(
    source_key: str,
    expected_header: str,
) -> None:
    """CopySource must be encoded exactly once across botocore signing and HTTP."""
    backend = _CopyCapabilityBackend()

    capability = await backend.presign_storage_copy_capability(source_key, "cas/dest.bin", ttl=30)

    assert backend.client.operation == "copy_object"
    assert backend.client.params == {
        "Bucket": "test-bucket",
        "Key": "cas/dest.bin",
        "CopySource": {"Bucket": "test-bucket", "Key": source_key},
    }
    assert backend.client.expires_in == 30
    assert capability.recovery_fence_ms == 30_000
    assert dict(capability.headers) == {"x-amz-copy-source": expected_header}


@pytest.mark.asyncio
async def test_facade_rejects_generic_cas_mutation_escape_hatches_before_backend_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Public raw upload helpers cannot become an unfenced second CAS publication path."""
    from app.core.storage import facade

    def backend_must_not_be_resolved() -> S3Backend:
        raise AssertionError("CAS escape hatch reached the storage backend")

    monkeypatch.setattr(facade, "get_storage", backend_must_not_be_resolved)
    parts = [{"PartNumber": 1, "ETag": '"etag"'}]

    async def invoke(name: str) -> None:
        match name:
            case "create-multipart":
                await facade.create_multipart_upload("cas/x")
            case "upload-part":
                await facade.upload_part("cas/x", "upload-id", 1, b"x")
            case "complete-multipart":
                await facade.complete_multipart_upload("cas/x", "upload-id", parts)
            case "presigned-part":
                await facade.generate_presigned_upload_part("cas/x", "upload-id", 1)
            case "generic-presigned-put":
                await facade.generate_presigned_put("cas/x", "application/octet-stream")
            case "metadata-rewrite":
                await facade.update_object_content_type("cas/x", "application/octet-stream")
            case _:
                raise AssertionError(name)

    for operation in (
        "create-multipart",
        "upload-part",
        "complete-multipart",
        "presigned-part",
        "generic-presigned-put",
        "metadata-rewrite",
    ):
        with pytest.raises(ValueError, match="cannot target canonical cas/ directly"):
            await invoke(operation)


class _ClockProbeClient:
    def __init__(self, date_header: str) -> None:
        self.date_header = date_header
        self.probes = 0

    async def head_bucket(self, **_kwargs: Any) -> dict[str, Any]:
        self.probes += 1
        return {"ResponseMetadata": {"HTTPHeaders": {"date": self.date_header}}}


@pytest.mark.asyncio
async def test_presigned_recovery_fence_measures_positive_signer_skew_as_duration() -> None:
    backend = object.__new__(S3Backend)
    client = _ClockProbeClient("Sun, 09 Aug 2026 21:50:00 GMT")
    url = "https://storage.invalid/bucket/cas/x?X-Amz-Date=20260809T220000Z&X-Amz-Expires=60"

    fence_ms = await backend._presigned_recovery_fence_ms(
        client, bucket="bucket", url=url, requested_ttl=60
    )

    assert fence_ms == (60 + 10 * 60) * 1000 + 2_000
    assert client.probes == 1


@pytest.mark.asyncio
async def test_presigned_recovery_fence_never_shortens_ttl_for_negative_signer_skew() -> None:
    backend = object.__new__(S3Backend)
    client = _ClockProbeClient("Sun, 09 Aug 2026 22:10:00 GMT")
    url = "https://storage.invalid/bucket/cas/x?X-Amz-Date=20260809T220000Z&X-Amz-Expires=60"

    fence_ms = await backend._presigned_recovery_fence_ms(
        client, bucket="bucket", url=url, requested_ttl=60
    )

    assert fence_ms == 62_000


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url,date_header,match",
    [
        (
            "https://storage.invalid/bucket/cas/x?X-Amz-Expires=60",
            "Sun, 09 Aug 2026 22:00:00 GMT",
            "invalid SigV4 expiry fields",
        ),
        (
            "https://storage.invalid/bucket/cas/x?X-Amz-Date=20260809T220000Z&X-Amz-Expires=61",
            "Sun, 09 Aug 2026 22:00:00 GMT",
            "expiry differs from requested TTL",
        ),
        (
            "https://storage.invalid/bucket/cas/x?X-Amz-Date=20260809T220000Z&X-Amz-Expires=60",
            "not-a-date",
            "valid Date clock probe",
        ),
    ],
)
async def test_presigned_recovery_fence_fails_closed_on_unverifiable_expiry(
    url: str,
    date_header: str,
    match: str,
) -> None:
    backend = object.__new__(S3Backend)
    client = _ClockProbeClient(date_header)

    with pytest.raises(RuntimeError, match=match):
        await backend._presigned_recovery_fence_ms(
            client, bucket="bucket", url=url, requested_ttl=60
        )
