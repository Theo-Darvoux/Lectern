from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from botocore.exceptions import ClientError

from app.core.storage.backends import SeaweedFSBackend
from app.core.storage.s3 import S3Backend


class _ChunkedBody:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = iter(chunks)
        self.closed = False

    async def read(self, _size: int = -1) -> bytes:
        return next(self._chunks, b"")

    def close(self) -> None:
        self.closed = True


class _RepeatingBody:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.read_calls = 0
        self.closed = False

    async def read(self, _size: int = -1) -> bytes:
        self.read_calls += 1
        return self.payload

    def close(self) -> None:
        self.closed = True


@asynccontextmanager
async def _client_context(client: Any) -> AsyncIterator[Any]:
    yield client


@pytest.mark.asyncio
async def test_read_full_object_collects_short_stream_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"first" + b"-second" + b"-third"
    body = _ChunkedBody([b"first", b"-second", b"-third", b""])

    class Client:
        async def get_object(self, **_kwargs: Any) -> dict[str, Any]:
            return {"Body": body, "ContentLength": len(payload)}

    backend = S3Backend()
    monkeypatch.setattr(backend, "_client", lambda _cfg=None: _client_context(Client()))

    assert await backend.read_full_object("key") == payload
    assert body.closed


@pytest.mark.asyncio
async def test_read_full_object_stops_at_advertised_content_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"small content"
    body = _RepeatingBody(payload)

    class Client:
        async def get_object(self, **_kwargs: Any) -> dict[str, Any]:
            return {"Body": body, "ContentLength": len(payload)}

    backend = S3Backend()
    monkeypatch.setattr(backend, "_client", lambda _cfg=None: _client_context(Client()))

    assert await backend.read_full_object("key") == payload
    assert body.read_calls == 1
    assert body.closed


@pytest.mark.asyncio
async def test_read_full_object_enforces_limit_across_short_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _ChunkedBody([b"12345", b"6789", b""])

    class Client:
        async def get_object(self, **_kwargs: Any) -> dict[str, Any]:
            return {"Body": body}

    backend = S3Backend()
    monkeypatch.setattr(backend, "_client", lambda _cfg=None: _client_context(Client()))
    monkeypatch.setattr("app.core.storage.s3._READ_FULL_OBJECT_MAX_BYTES", 8)

    with pytest.raises(ValueError, match="read_full_object limit"):
        await backend.read_full_object("key")
    assert body.closed


@pytest.mark.asyncio
async def test_read_full_object_rejects_eof_before_advertised_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _ChunkedBody([b"truncated", b""])

    class Client:
        async def get_object(self, **_kwargs: Any) -> dict[str, Any]:
            return {"Body": body, "ContentLength": 100}

    backend = S3Backend()
    monkeypatch.setattr(backend, "_client", lambda _cfg=None: _client_context(Client()))

    with pytest.raises(ValueError, match=r"size changed during read \(9 != 100\)"):
        await backend.read_full_object("key")
    assert body.closed


@pytest.mark.asyncio
async def test_read_object_bytes_treats_empty_invalid_range_as_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Client:
        exceptions = SimpleNamespace(ClientError=ClientError)

        async def get_object(self, **_kwargs: Any) -> dict[str, Any]:
            raise ClientError(
                {
                    "Error": {
                        "Code": "InvalidRange",
                        "Message": "The requested range is not satisfiable",
                    },
                    "ResponseMetadata": {"HTTPStatusCode": 416},
                },
                "GetObject",
            )

        async def head_object(self, **_kwargs: Any) -> dict[str, Any]:
            return {"ContentLength": 0}

    backend = S3Backend()
    monkeypatch.setattr(backend, "_client", lambda _cfg=None: _client_context(Client()))

    assert await backend.read_object_bytes("empty", 32) == b""


@pytest.mark.asyncio
async def test_read_object_bytes_zero_count_avoids_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = S3Backend()

    def fail_client(_cfg=None):
        raise AssertionError("storage client should not be opened")

    monkeypatch.setattr(backend, "_client", fail_client)

    assert await backend.read_object_bytes("key", 0) == b""


@pytest.mark.asyncio
async def test_seaweedfs_content_type_update_reuploads_raw_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_key = "files/example.bin"
    payload = b"header-preservation"
    body = _ChunkedBody([payload, b""])

    class Client:
        def __init__(self) -> None:
            self.metadata: dict[str, Any] = {
                "ContentLength": len(payload),
                "ContentType": "application/octet-stream",
                "ContentEncoding": "gzip",
                "ContentDisposition": 'inline; filename="old.bin"',
                "CacheControl": "public, max-age=86400",
                "Metadata": {"owner": "lectern"},
            }
            self.uploaded = b""
            self.pending_metadata: dict[str, Any] | None = None

        async def head_object(self, **_kwargs: Any) -> dict[str, Any]:
            return dict(self.metadata)

        async def get_object(self, **_kwargs: Any) -> dict[str, Any]:
            return {"Body": body}

        async def create_multipart_upload(self, **kwargs: Any) -> dict[str, str]:
            self.pending_metadata = {
                "ContentLength": len(payload),
                "ContentType": kwargs["ContentType"],
                "ContentEncoding": kwargs.get("ContentEncoding"),
                "ContentDisposition": kwargs.get("ContentDisposition"),
                "CacheControl": kwargs.get("CacheControl"),
                "Metadata": kwargs.get("Metadata", {}),
            }
            return {"UploadId": "rewrite-upload"}

        async def upload_part(self, **kwargs: Any) -> dict[str, str]:
            assert kwargs["UploadId"] == "rewrite-upload"
            assert kwargs["PartNumber"] == 1
            self.uploaded += bytes(kwargs["Body"])
            return {"ETag": '"part-1"'}

        async def complete_multipart_upload(self, **kwargs: Any) -> None:
            assert kwargs["UploadId"] == "rewrite-upload"
            assert kwargs["MultipartUpload"] == {
                "Parts": [{"PartNumber": 1, "ETag": '"part-1"'}]
            }
            assert self.pending_metadata is not None
            self.metadata = self.pending_metadata

    client = Client()
    backend = SeaweedFSBackend()
    monkeypatch.setattr(backend, "_client", lambda _cfg=None: _client_context(client))
    monkeypatch.setattr(
        backend,
        "_cfg",
        lambda: {
            "bucket": "test-bucket",
            "endpoint": "localhost:8333",
            "access_key": "key",
            "secret_key": "secret",
            "region": "us-east-1",
            "use_ssl": False,
            "public_endpoint": None,
        },
    )

    await backend.update_object_content_type(source_key, "application/x-custom")

    assert client.uploaded == payload
    assert body.closed
    assert client.metadata == {
        "ContentLength": len(payload),
        "ContentType": "application/x-custom",
        "ContentEncoding": "gzip",
        "ContentDisposition": 'inline; filename="old.bin"',
        "CacheControl": "public, max-age=86400",
        "Metadata": {"owner": "lectern"},
    }
