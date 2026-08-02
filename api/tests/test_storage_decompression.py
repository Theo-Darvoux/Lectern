import gzip
import io
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.storage.facade import download_file
from app.core.storage.s3 import (
    MULTIPART_THRESHOLD,
    S3Backend,
    _decompress_gzip_file,
)


def test_backend_uses_its_own_configuration() -> None:
    """A directly instantiated backend is isolated from the facade singleton."""
    import app.core.storage.facade as facade

    global_backend = S3Backend()
    isolated_backend = S3Backend()

    with (
        patch.object(global_backend, "_settings", return_value={"bucket": "global"}),
        patch.object(isolated_backend, "_settings", return_value={"bucket": "isolated"}),
        patch.object(facade, "_storage", global_backend),
    ):
        assert isolated_backend._cfg() == {"bucket": "isolated"}


async def test_generic_storage_delete_has_no_upload_quota_side_effect() -> None:
    """Quota ownership stays with upload workflows, not the storage adapter."""
    client = AsyncMock()
    client_context = AsyncMock()
    client_context.__aenter__ = AsyncMock(return_value=client)
    client_context.__aexit__ = AsyncMock(return_value=False)
    redis = AsyncMock()

    with (
        patch("app.core.storage.s3.S3Backend._settings", return_value={"bucket": "bucket"}),
        patch(
            "app.core.storage.s3.S3Backend.get_s3_client",
            return_value=client_context,
        ),
        patch("app.core.database.redis.redis_client", redis),
    ):
        await S3Backend().delete_object("quarantine/user/upload/file.pdf")

    client.delete_object.assert_awaited_once_with(
        Bucket="bucket",
        Key="quarantine/user/upload/file.pdf",
    )
    redis.zrem.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_file_decompresses_gzip_when_explicitly_requested() -> None:
    """Gzip expansion is explicit so security-sensitive callers receive raw bytes."""
    # Create gzipped test content
    original_text = b"Hello, this is a compressed test file."
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as f:
        f.write(original_text)
    gzipped_data = buf.getvalue()

    # Mock response from S3
    mock_body = AsyncMock()
    # Mock stream read behavior
    chunks = [gzipped_data]

    async def mock_read(amt=None):
        if not chunks:
            return b""
        return chunks.pop(0)

    mock_body.read = mock_read
    mock_body.close = MagicMock()

    mock_response = {
        "Body": mock_body,
        "ContentEncoding": "gzip",
    }

    mock_client = AsyncMock()
    mock_client.get_object = AsyncMock(return_value=mock_response)

    mock_client_context = AsyncMock()
    mock_client_context.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_context.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.core.storage.s3.S3Backend._settings", return_value={"bucket": "test-bucket"}),
        patch("app.core.storage.s3.S3Backend.get_s3_client", return_value=mock_client_context),
    ):
        with tempfile.TemporaryDirectory() as tmpdir:
            dest_path = Path(tmpdir) / "downloaded.txt"
            await download_file("cas/test_key", dest_path, decompress=True)

            # Assert file exists and is decompressed
            assert dest_path.exists()
            assert dest_path.read_bytes() == original_text


def test_gzip_expansion_over_limit_is_rejected(tmp_path: Path) -> None:
    compressed = tmp_path / "bomb.gz"
    compressed.write_bytes(gzip.compress(b"A" * 1024))

    with pytest.raises(ValueError, match="decompressed size limit"):
        _decompress_gzip_file(compressed, max_output_bytes=100)

    assert compressed.read_bytes().startswith(b"\x1f\x8b")
    assert not compressed.with_suffix(".gz.decompressed.tmp").exists()


@pytest.mark.asyncio
async def test_hashed_download_rejects_body_larger_than_limit(tmp_path: Path) -> None:
    body = AsyncMock()
    body.read.side_effect = [b"1234", b""]
    body.close = MagicMock()
    client = AsyncMock()
    client.get_object.return_value = {"Body": body}
    client_context = AsyncMock()
    client_context.__aenter__.return_value = client
    client_context.__aexit__.return_value = False
    destination = tmp_path / "download.bin"

    with (
        patch("app.core.storage.s3.S3Backend._settings", return_value={"bucket": "bucket"}),
        patch("app.core.storage.s3.S3Backend.get_s3_client", return_value=client_context),
        pytest.raises(ValueError, match="download size limit"),
    ):
        await S3Backend().download_file_with_hash(
            "quarantine/object", destination, max_bytes=3
        )

    assert destination.read_bytes() == b""
    body.close.assert_called_once()


@pytest.mark.asyncio
async def test_hashed_download_rejects_head_size_mismatch(tmp_path: Path) -> None:
    body = AsyncMock()
    body.read.side_effect = [b"1234", b""]
    body.close = MagicMock()
    client = AsyncMock()
    client.get_object.return_value = {"Body": body}
    client_context = AsyncMock()
    client_context.__aenter__.return_value = client
    client_context.__aexit__.return_value = False
    destination = tmp_path / "download.bin"

    with (
        patch("app.core.storage.s3.S3Backend._settings", return_value={"bucket": "bucket"}),
        patch("app.core.storage.s3.S3Backend.get_s3_client", return_value=client_context),
        pytest.raises(ValueError, match="size changed during download"),
    ):
        await S3Backend().download_file_with_hash(
            "quarantine/object", destination, expected_size=5
        )

    assert destination.read_bytes() == b"1234"
    body.close.assert_called_once()


@pytest.mark.asyncio
async def test_multipart_rejects_custom_chunk_below_s3_minimum(tmp_path: Path) -> None:
    source = tmp_path / "large.bin"
    source.write_bytes(b"x" * MULTIPART_THRESHOLD)
    backend = S3Backend()
    backend.create_multipart_upload = AsyncMock()

    with pytest.raises(ValueError, match="at least 5 MiB"):
        await backend.upload_file_multipart(
            source,
            "cas/object",
            chunk_size=MULTIPART_THRESHOLD - 1,
        )

    backend.create_multipart_upload.assert_not_awaited()
