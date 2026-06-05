"""Unit tests for the new S3Backend methods added for lossless backup.

Covers:
  - download_file_raw: stores exact bytes, never decompresses gzip
  - get_object_headers: returns the four metadata headers; handles missing ones
"""

from __future__ import annotations

import gzip
import io
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.storage import download_file, download_file_raw, get_object_headers


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_s3_ctx(mock_client: AsyncMock) -> AsyncMock:
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _make_streaming_body(data: bytes) -> AsyncMock:
    chunks = [data]

    async def _read(amt: int = -1) -> bytes:
        return chunks.pop(0) if chunks else b""

    body = AsyncMock()
    body.read = _read
    body.close = MagicMock()
    return body


_S3_SETTINGS = {"bucket": "test-bucket"}
_SETTINGS_PATH = "app.core.storage._get_s3_settings"
_CLIENT_PATH = "app.core.storage.get_s3_client"


# ── download_file_raw ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_download_file_raw_stores_exact_bytes(tmp_path: Path) -> None:
    """Raw bytes written to disk must equal what S3 returns, byte-for-byte."""
    payload = b"exact bytes from S3"
    client = AsyncMock()
    client.get_object = AsyncMock(return_value={"Body": _make_streaming_body(payload)})

    with (
        patch(_SETTINGS_PATH, return_value=_S3_SETTINGS),
        patch(_CLIENT_PATH, return_value=_make_s3_ctx(client)),
    ):
        dest = tmp_path / "out.bin"
        await download_file_raw("cas/some_key", dest)

    assert dest.read_bytes() == payload


@pytest.mark.asyncio
async def test_download_file_raw_does_not_decompress_gzip(tmp_path: Path) -> None:
    """download_file_raw must NOT decompress gzip-encoded objects."""
    original = b"important raw content"
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as f:
        f.write(original)
    gzip_bytes = buf.getvalue()

    client = AsyncMock()
    client.get_object = AsyncMock(
        return_value={"Body": _make_streaming_body(gzip_bytes), "ContentEncoding": "gzip"}
    )

    with (
        patch(_SETTINGS_PATH, return_value=_S3_SETTINGS),
        patch(_CLIENT_PATH, return_value=_make_s3_ctx(client)),
    ):
        dest = tmp_path / "out.gz"
        await download_file_raw("cas/gzip_key", dest)

    # File on disk must still be gzip — NOT the decompressed payload
    assert dest.read_bytes() == gzip_bytes
    assert dest.read_bytes() != original


@pytest.mark.asyncio
async def test_download_file_decompresses_but_raw_does_not(tmp_path: Path) -> None:
    """Contrast: download_file decompresses; download_file_raw preserves gzip."""
    original = b"compare me"
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as f:
        f.write(original)
    gzip_bytes = buf.getvalue()

    def _make_resp():
        return {"Body": _make_streaming_body(gzip_bytes), "ContentEncoding": "gzip"}

    client = AsyncMock()

    # First call → download_file (decompresses)
    client.get_object = AsyncMock(return_value=_make_resp())
    with (
        patch(_SETTINGS_PATH, return_value=_S3_SETTINGS),
        patch(_CLIENT_PATH, return_value=_make_s3_ctx(client)),
    ):
        dest_decomp = tmp_path / "decomp"
        await download_file("cas/k", dest_decomp)

    # Second call → download_file_raw (keeps gzip)
    client.get_object = AsyncMock(return_value=_make_resp())
    with (
        patch(_SETTINGS_PATH, return_value=_S3_SETTINGS),
        patch(_CLIENT_PATH, return_value=_make_s3_ctx(client)),
    ):
        dest_raw = tmp_path / "raw"
        await download_file_raw("cas/k", dest_raw)

    assert dest_decomp.read_bytes() == original
    assert dest_raw.read_bytes() == gzip_bytes


@pytest.mark.asyncio
async def test_download_file_raw_non_gzip_object(tmp_path: Path) -> None:
    """Non-gzip objects work identically to download_file (no encoding header)."""
    payload = b"plain pdf bytes"
    client = AsyncMock()
    client.get_object = AsyncMock(return_value={"Body": _make_streaming_body(payload)})

    with (
        patch(_SETTINGS_PATH, return_value=_S3_SETTINGS),
        patch(_CLIENT_PATH, return_value=_make_s3_ctx(client)),
    ):
        dest = tmp_path / "plain.pdf"
        await download_file_raw("uploads/u1/plain.pdf", dest)

    assert dest.read_bytes() == payload


@pytest.mark.asyncio
async def test_download_file_raw_streams_in_chunks(tmp_path: Path) -> None:
    """Large body is assembled correctly even when delivered in multiple chunks."""
    part1, part2, part3 = b"aaa", b"bbb", b"ccc"
    chunks = [part1, part2, part3]

    body = AsyncMock()
    body.read = AsyncMock(side_effect=chunks + [b""])
    body.close = MagicMock()

    client = AsyncMock()
    client.get_object = AsyncMock(return_value={"Body": body})

    with (
        patch(_SETTINGS_PATH, return_value=_S3_SETTINGS),
        patch(_CLIENT_PATH, return_value=_make_s3_ctx(client)),
    ):
        dest = tmp_path / "chunked"
        await download_file_raw("cas/chunked", dest)

    assert dest.read_bytes() == part1 + part2 + part3


# ── get_object_headers ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_object_headers_all_present() -> None:
    """All four headers returned when S3 supplies them."""
    client = AsyncMock()
    client.head_object = AsyncMock(
        return_value={
            "ContentType": "application/pdf",
            "ContentEncoding": "gzip",
            "ContentDisposition": "attachment; filename=\"file.pdf\"",
            "CacheControl": "public, max-age=86400",
        }
    )

    with (
        patch(_SETTINGS_PATH, return_value=_S3_SETTINGS),
        patch(_CLIENT_PATH, return_value=_make_s3_ctx(client)),
    ):
        headers = await get_object_headers("cas/abc")

    assert headers["content_type"] == "application/pdf"
    assert headers["content_encoding"] == "gzip"
    assert headers["content_disposition"] == "attachment; filename=\"file.pdf\""
    assert headers["cache_control"] == "public, max-age=86400"


@pytest.mark.asyncio
async def test_get_object_headers_missing_encoding_and_disposition() -> None:
    """Optional headers (encoding, disposition) are None when absent."""
    client = AsyncMock()
    client.head_object = AsyncMock(
        return_value={"ContentType": "video/mp4", "CacheControl": "public, max-age=86400"}
    )

    with (
        patch(_SETTINGS_PATH, return_value=_S3_SETTINGS),
        patch(_CLIENT_PATH, return_value=_make_s3_ctx(client)),
    ):
        headers = await get_object_headers("cas/video")

    assert headers["content_type"] == "video/mp4"
    assert headers["content_encoding"] is None
    assert headers["content_disposition"] is None
    assert headers["cache_control"] == "public, max-age=86400"


@pytest.mark.asyncio
async def test_get_object_headers_all_absent() -> None:
    """Empty HEAD response returns all None values."""
    client = AsyncMock()
    client.head_object = AsyncMock(return_value={})

    with (
        patch(_SETTINGS_PATH, return_value=_S3_SETTINGS),
        patch(_CLIENT_PATH, return_value=_make_s3_ctx(client)),
    ):
        headers = await get_object_headers("cas/empty")

    assert headers == {
        "content_type": None,
        "content_encoding": None,
        "content_disposition": None,
        "cache_control": None,
    }


@pytest.mark.asyncio
async def test_get_object_headers_uses_head_not_get() -> None:
    """get_object_headers must call head_object, never get_object (no body download)."""
    client = AsyncMock()
    client.head_object = AsyncMock(return_value={"ContentType": "image/png"})
    client.get_object = AsyncMock()

    with (
        patch(_SETTINGS_PATH, return_value=_S3_SETTINGS),
        patch(_CLIENT_PATH, return_value=_make_s3_ctx(client)),
    ):
        await get_object_headers("branding/logo.png")

    client.head_object.assert_called_once()
    client.get_object.assert_not_called()
