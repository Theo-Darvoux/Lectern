import gzip
import io
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.storage import download_file


@pytest.mark.asyncio
async def test_download_file_decompresses_gzip() -> None:
    """Test that download_file automatically decompresses objects uploaded with Content-Encoding: gzip."""
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
        patch(
            "app.core.storage._get_s3_settings", AsyncMock(return_value={"bucket": "test-bucket"})
        ),
        patch("app.core.storage.get_s3_client", return_value=mock_client_context),
    ):
        with tempfile.TemporaryDirectory() as tmpdir:
            dest_path = Path(tmpdir) / "downloaded.txt"
            await download_file("cas/test_key", dest_path)

            # Assert file exists and is decompressed
            assert dest_path.exists()
            assert dest_path.read_bytes() == original_text
