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
