"""Resource-admission regression tests for batch ZIP extraction."""

import asyncio
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.config import settings
from app.core.common.exceptions import BadRequestError
from app.routers.upload import batch_zip


def test_extract_zip_rejects_insufficient_disk_before_writing(tmp_path) -> None:
    archive_path = tmp_path / "upload.zip"
    extraction_path = tmp_path / "extracted"
    extraction_path.mkdir()
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("document.txt", b"hello")

    no_free_space = SimpleNamespace(total=1, used=1, free=0)
    with (
        patch("app.routers.upload.batch_zip.shutil.disk_usage", return_value=no_free_space),
        pytest.raises(BadRequestError, match="Insufficient temporary disk space"),
    ):
        batch_zip._extract_zip_sync(str(archive_path), str(extraction_path), max_members=10)

    assert list(extraction_path.iterdir()) == []


@pytest.mark.asyncio
async def test_cancelled_extraction_holds_global_slot_until_child_cleanup_finishes() -> None:
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    calls = 0

    async def blocking_extract(*_args: object, **_kwargs: object) -> tuple[list[object], list[str]]:
        nonlocal calls
        calls += 1
        call_number = calls
        if call_number == 1:
            first_started.set()
            try:
                await release_first.wait()
            except asyncio.CancelledError:
                await release_first.wait()
                raise
        return [], []

    with (
        patch("app.routers.upload.batch_zip._EXTRACTION_SEMAPHORE", asyncio.Semaphore(1)),
        patch("app.routers.upload.batch_zip.extract_zip_isolated", side_effect=blocking_extract),
    ):
        first = asyncio.create_task(batch_zip._extract_zip_bounded("one", "tmp", 1))
        await asyncio.wait_for(first_started.wait(), timeout=1)

        first.cancel()
        second = asyncio.create_task(batch_zip._extract_zip_bounded("two", "tmp", 1))
        await asyncio.sleep(0.05)
        assert calls == 1

        release_first.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        await second

    assert calls == 2


def test_extract_zip_rejects_unicode_normalized_traversal(tmp_path) -> None:
    archive_path = tmp_path / "upload.zip"
    extraction_path = tmp_path / "extracted"
    extraction_path.mkdir()
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("．．／escape.txt", b"payload")

    with pytest.raises(BadRequestError, match="unsafe path"):
        batch_zip._extract_zip_sync(str(archive_path), str(extraction_path), max_members=10)


def test_extract_zip_rejects_casefold_and_hierarchy_collisions(tmp_path) -> None:
    extraction_path = tmp_path / "extracted"
    extraction_path.mkdir()

    casefold_archive = tmp_path / "casefold.zip"
    with zipfile.ZipFile(casefold_archive, "w") as archive:
        archive.writestr("File.txt", b"one")
        archive.writestr("file.txt", b"two")
    with pytest.raises(BadRequestError, match="colliding file paths"):
        batch_zip._extract_zip_sync(str(casefold_archive), str(extraction_path), max_members=10)

    hierarchy_archive = tmp_path / "hierarchy.zip"
    with zipfile.ZipFile(hierarchy_archive, "w") as archive:
        archive.writestr("folder", b"file")
        archive.writestr("folder/child.txt", b"child")
    with pytest.raises(BadRequestError, match="colliding file paths"):
        batch_zip._extract_zip_sync(str(hierarchy_archive), str(extraction_path), max_members=10)


def test_extract_zip_persists_canonical_relative_path(tmp_path) -> None:
    archive_path = tmp_path / "canonical.zip"
    extraction_path = tmp_path / "extracted"
    extraction_path.mkdir()
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("Ｆｏｌｄｅｒ／file.txt", b"payload")

    entries, skipped = batch_zip._extract_zip_sync(
        str(archive_path), str(extraction_path), max_members=10
    )
    assert skipped == []
    assert entries[0].relative_path == "Folder/file.txt"
    assert entries[0].filename == "file.txt"


@pytest.mark.asyncio
async def test_isolated_batch_extraction_uses_the_largest_admitted_file_limit(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Batch extraction must not reject a member accepted by per-MIME upload policy."""
    monkeypatch.setattr(settings, "processing_root", str(tmp_path))
    monkeypatch.setattr(settings, "sandbox_file_size_limit_mb", 1)
    archive_path = tmp_path / "large-member.zip"
    extraction_path = tmp_path / "extracted-large"
    extraction_path.mkdir()
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("lecture.mp4", b"0" * (2 * 1024 * 1024))

    entries, skipped = await batch_zip._extract_zip_bounded(
        str(archive_path), str(extraction_path), max_members=10
    )

    assert skipped == []
    assert entries[0].size == 2 * 1024 * 1024
