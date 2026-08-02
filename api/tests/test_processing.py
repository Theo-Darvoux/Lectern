import hashlib
import io
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import UploadFile

from app.core.common.exceptions import BadRequestError
from app.core.events.processing import ProcessingFile


@pytest.fixture
def mock_upload_file():
    """Mock an UploadFile with specific content."""
    content = b"x" * (128 * 1024)  # 128 KiB

    mock = AsyncMock(spec=UploadFile)

    # Needs a real implementation of read() for chunking behaviour
    stream = io.BytesIO(content)

    async def mock_read(size: int = -1):
        return stream.read(size)

    mock.read = mock_read

    return mock, content


@pytest.mark.asyncio
async def test_from_upload_creates_temp_file(mock_upload_file):
    upload, content = mock_upload_file
    pf = await ProcessingFile.from_upload(upload, max_bytes=len(content) + 1)

    assert isinstance(pf.path, Path)
    assert pf.path.exists()
    assert pf.size == len(content)

    with open(pf.path, "rb") as f:
        assert f.read() == content

    pf.cleanup()
    assert not pf.path.exists()


@pytest.mark.asyncio
async def test_from_upload_enforces_size(mock_upload_file):
    upload, content = mock_upload_file
    max_bytes = 64 * 1024  # 64 KiB (smaller than content)

    with pytest.raises(BadRequestError, match="File size exceeds maximum"):
        await ProcessingFile.from_upload(upload, max_bytes=max_bytes)


@pytest.mark.asyncio
async def test_sha256_matches_hashlib(tmp_path):
    temp = tmp_path / "test.bin"
    content = os.urandom(256 * 1024)
    temp.write_bytes(content)

    pf = ProcessingFile(temp, len(content))
    expected_hash = hashlib.sha256(content).hexdigest()

    actual_hash = await pf.sha256()
    assert actual_hash == expected_hash


@pytest.mark.asyncio
async def test_replace_with_deletes_old(tmp_path):
    old = tmp_path / "old.bin"
    old.write_bytes(b"old")

    new = tmp_path / "new.bin"
    new.write_bytes(b"new")

    pf = ProcessingFile(old, 3, hash=hashlib.sha256(b"old").hexdigest())
    await pf.replace_with(new)

    assert not old.exists()
    assert pf.path == new
    assert pf.size == 3
    assert pf.read_bytes() == b"new"
    assert await pf.sha256() == hashlib.sha256(b"new").hexdigest()


@pytest.mark.asyncio
async def test_replace_with_missing_file_preserves_original(tmp_path):
    old = tmp_path / "old.bin"
    old.write_bytes(b"last-good-copy")
    pf = ProcessingFile(old, old.stat().st_size, hash="old-hash")

    with pytest.raises(FileNotFoundError, match="Replacement file was not created"):
        await pf.replace_with(tmp_path / "missing.bin")

    assert pf.path == old
    assert pf.hash == "old-hash"
    assert old.read_bytes() == b"last-good-copy"


def test_cleanup_idempotent(tmp_path):
    temp = tmp_path / "test.bin"
    temp.write_bytes(b"test")

    pf = ProcessingFile(temp, 4)
    pf.cleanup()
    assert not temp.exists()

    # Should not raise
    pf.cleanup()
