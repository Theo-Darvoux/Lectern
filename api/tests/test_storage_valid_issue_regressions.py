from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.core.storage.backends import SeaweedFSBackend
from app.workers.upload.exceptions import UploadError
from app.workers.upload.pipeline import UploadPipeline

_MIB = 1024 * 1024


class _StreamingBody:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._offset = 0
        self.closed = False

    async def read(self, amount: int = -1) -> bytes:
        if self._offset >= len(self._payload):
            return b""
        if amount < 0:
            amount = len(self._payload) - self._offset
        # Deliberately return short reads to prove the rewrite accumulates a
        # complete S3 part rather than assuming one read fills the request.
        amount = max(1, min(amount, 257 * 1024))
        end = min(len(self._payload), self._offset + amount)
        chunk = self._payload[self._offset : end]
        self._offset = end
        return chunk

    def close(self) -> None:
        self.closed = True


class _FakeSeaweedClient:
    def __init__(self, payload: bytes, *, fail_part: int | None = None) -> None:
        self.payload = payload
        self.fail_part = fail_part
        self.body = _StreamingBody(payload)
        self.uploaded_parts: list[bytes] = []
        self.completed = False
        self.aborted = False
        self.put_calls = 0
        self._head_calls = 0

    async def head_object(self, **_kwargs: Any) -> dict[str, Any]:
        self._head_calls += 1
        return {
            "ContentLength": len(self.payload),
            "ContentType": "application/x-new"
            if self._head_calls > 1
            else "application/octet-stream",
            "ContentEncoding": "gzip",
            "ContentDisposition": 'inline; filename="old.bin"',
            "CacheControl": "private, max-age=60",
            "Metadata": {"source": "regression"},
        }

    async def get_object(self, **_kwargs: Any) -> dict[str, Any]:
        return {"Body": self.body}

    async def create_multipart_upload(self, **kwargs: Any) -> dict[str, str]:
        assert kwargs["ContentType"] == "application/x-new"
        assert kwargs["ContentEncoding"] == "gzip"
        assert kwargs["ContentDisposition"] == 'inline; filename="old.bin"'
        assert kwargs["CacheControl"] == "private, max-age=60"
        assert kwargs["Metadata"] == {"source": "regression"}
        return {"UploadId": "rewrite-upload"}

    async def upload_part(self, **kwargs: Any) -> dict[str, str]:
        number = int(kwargs["PartNumber"])
        if self.fail_part == number:
            raise RuntimeError("injected rewrite part failure")
        body = bytes(kwargs["Body"])
        self.uploaded_parts.append(body)
        return {"ETag": f'"part-{number}"'}

    async def complete_multipart_upload(self, **kwargs: Any) -> None:
        assert kwargs["MultipartUpload"]["Parts"]
        self.completed = True

    async def abort_multipart_upload(self, **_kwargs: Any) -> None:
        self.aborted = True

    async def put_object(self, **_kwargs: Any) -> None:
        self.put_calls += 1


class _FakeSeaweedBackend(SeaweedFSBackend):
    def __init__(self, client: _FakeSeaweedClient) -> None:
        self.client = client

    def _cfg(self) -> dict[str, Any]:
        return {"bucket": "bucket"}

    @asynccontextmanager
    async def _client(self, _cfg: dict[str, Any]):  # type: ignore[override]
        yield self.client


def test_pipeline_remaining_budget_is_not_a_fixed_finalize_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = object.__new__(UploadPipeline)
    pipeline._elapsed = lambda: 125.0  # type: ignore[method-assign]
    monkeypatch.setattr(settings, "upload_pipeline_max_seconds", 600)

    assert pipeline._remaining_pipeline_seconds("finalizing") == 475.0

    pipeline._elapsed = lambda: 600.1  # type: ignore[method-assign]
    with pytest.raises(UploadError, match="Pipeline deadline exceeded"):
        pipeline._remaining_pipeline_seconds("finalizing")


def test_finalize_stage_has_no_independent_60_second_storage_deadline() -> None:
    import inspect

    from app.workers.upload.stages import finalize

    source = inspect.getsource(finalize.run_finalize_storage)
    assert "timeout=60.0" not in source
    assert "timeout=30.0" not in source
    assert "asyncio.wait_for" not in source


def _minimal_finalize_pipeline(timeout_seconds: float) -> UploadPipeline:
    pipeline = object.__new__(UploadPipeline)
    pipeline._check_deadline = lambda _stage: None  # type: ignore[method-assign]
    pipeline._remaining_pipeline_seconds = (  # type: ignore[method-assign]
        lambda _stage: timeout_seconds
    )
    pipeline._elapsed = lambda: float(settings.upload_pipeline_max_seconds)  # type: ignore[method-assign]
    pipeline.emit_status = AsyncMock()  # type: ignore[method-assign]
    pipeline._check_bazaar_before_finalize = AsyncMock()  # type: ignore[method-assign]
    pipeline.ctx = SimpleNamespace(db_sessionmaker=None)
    pipeline.pf = object()  # type: ignore[assignment]
    pipeline.user_id = "user"
    pipeline.upload_id = "upload"
    pipeline.original_filename = "file.bin"
    pipeline.original_sha256 = "a" * 64
    pipeline.cas_key = "cas:key"
    pipeline.initial_size = 1
    pipeline.mime_type = "application/octet-stream"
    pipeline.redis = object()
    pipeline.tracer = object()
    return pipeline


@pytest.mark.asyncio
async def test_finalize_stage_is_cancelled_by_remaining_pipeline_budget() -> None:
    pipeline = _minimal_finalize_pipeline(0.01)

    async def slow_finalize(*_args: Any, **_kwargs: Any) -> None:
        await asyncio.sleep(1)

    with (
        patch("app.workers.upload.pipeline.run_finalize_storage", side_effect=slow_finalize),
        pytest.raises(UploadError, match="Pipeline deadline exceeded at stage 'finalizing'"),
    ):
        await pipeline._fast_finalize_and_enqueue_post_scan()


@pytest.mark.asyncio
async def test_backend_timeout_is_not_mislabeled_as_pipeline_deadline() -> None:
    pipeline = _minimal_finalize_pipeline(10.0)

    with (
        patch(
            "app.workers.upload.pipeline.run_finalize_storage",
            side_effect=TimeoutError("backend timeout"),
        ),
        pytest.raises(TimeoutError, match="backend timeout"),
    ):
        await pipeline._fast_finalize_and_enqueue_post_scan()


@pytest.mark.asyncio
async def test_seaweedfs_content_type_rewrite_streams_multipart_without_single_put() -> None:
    payload = b"x" * (9 * _MIB + 17)
    client = _FakeSeaweedClient(payload)
    backend = _FakeSeaweedBackend(client)

    await backend.update_object_content_type("object.bin", "application/x-new")

    assert client.put_calls == 0
    assert client.completed is True
    assert client.aborted is False
    assert client.body.closed is True
    assert b"".join(client.uploaded_parts) == payload
    assert len(client.uploaded_parts) == 2
    assert len(client.uploaded_parts[0]) == 8 * _MIB
    assert len(client.uploaded_parts[1]) == _MIB + 17


@pytest.mark.asyncio
async def test_seaweedfs_metadata_rewrite_aborts_multipart_on_part_failure() -> None:
    payload = b"y" * (9 * _MIB + 17)
    client = _FakeSeaweedClient(payload, fail_part=2)
    backend = _FakeSeaweedBackend(client)

    with pytest.raises(RuntimeError, match="injected rewrite part failure"):
        await backend.update_object_content_type("object.bin", "application/x-new")

    assert client.completed is False
    assert client.aborted is True
    assert client.put_calls == 0
    assert client.body.closed is True
