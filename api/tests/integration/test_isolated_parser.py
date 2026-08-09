from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from app.config import settings
from app.core.common.exceptions import BadRequestError
from app.core.security.isolated_parser import (
    extract_office_thumbnail_isolated,
    inspect_upload,
    process_avatar_isolated,
    render_thumbnail_isolated,
    sanitize_upload,
)
from app.core.security.scanner import MalwareScanner
from app.routers.upload import batch_zip

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("bwrap") is None, reason="bwrap is not installed"),
]


@pytest.mark.asyncio
async def test_upload_inspection_runs_in_an_unprivileged_isolated_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processing_root = tmp_path / "processing"
    processing_root.mkdir()
    source = processing_root / "safe.txt"
    source.write_text("safe text", encoding="utf-8")

    monkeypatch.setattr(settings, "processing_root", str(processing_root))
    result = await inspect_upload(
        source,
        filename="safe.txt",
        declared_mime="text/plain",
        inspect_archive=False,
    )

    assert result.actual_mime == "text/plain"
    assert result.uncompressed_size is None
    assert result.parser_pid != os.getpid()
    assert result.parser_uid == os.getuid()


@pytest.mark.asyncio
async def test_image_sanitization_returns_only_child_generated_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processing_root = tmp_path / "processing"
    processing_root.mkdir()
    source = processing_root / "pixel.png"
    Image.new("RGB", (1, 1), "white").save(source)

    monkeypatch.setattr(settings, "processing_root", str(processing_root))
    sanitized = await sanitize_upload(source, mime_type="image/png")

    assert sanitized != source
    assert sanitized.is_file()
    assert sanitized.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.asyncio
async def test_avatar_rendering_runs_in_the_disposable_parser_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processing_root = tmp_path / "processing"
    processing_root.mkdir()
    source = processing_root / "avatar.png"
    Image.new("RGB", (8, 8), "blue").save(source)
    monkeypatch.setattr(settings, "processing_root", str(processing_root))

    result = await process_avatar_isolated(source, size=4, quality=80)

    assert result.startswith(b"RIFF")
    assert b"WEBP" in result[:16]


@pytest.mark.asyncio
async def test_alpha_thumbnail_is_flattened_in_the_disposable_parser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processing_root = tmp_path / "processing"
    processing_root.mkdir()
    source = processing_root / "alpha.png"
    output = processing_root / "alpha.webp"
    Image.new("RGBA", (8, 8), (0, 0, 255, 128)).save(source)
    monkeypatch.setattr(settings, "processing_root", str(processing_root))

    blank = await render_thumbnail_isolated(
        source,
        output,
        size=(4, 4),
        quality=80,
        flatten_alpha=True,
    )

    assert blank is False
    assert output.read_bytes().startswith(b"RIFF")


@pytest.mark.asyncio
async def test_office_fallback_without_an_image_leaves_no_false_thumbnail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processing_root = tmp_path / "processing"
    processing_root.mkdir()
    source = processing_root / "document.docx"
    output = processing_root / "fallback.webp"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("word/document.xml", "<document/>")
    monkeypatch.setattr(settings, "processing_root", str(processing_root))

    produced = await extract_office_thumbnail_isolated(
        source,
        output,
        size=(4, 4),
        quality=80,
    )

    assert produced is False
    assert not output.exists()


@pytest.mark.asyncio
async def test_yara_matches_hostile_bytes_without_parsing_them_in_the_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processing_root = tmp_path / "processing"
    processing_root.mkdir()
    source = processing_root / "eicar.txt"
    source.write_text(
        "X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*",
        encoding="ascii",
    )
    monkeypatch.setattr(settings, "processing_root", str(processing_root))

    scanner = MalwareScanner()
    scanner.initialize()

    class ParentProcessRulesTrap:
        def match(self, **_kwargs: object) -> object:
            raise AssertionError("hostile bytes reached the worker's in-process YARA object")

    scanner.rules = ParentProcessRulesTrap()  # type: ignore[assignment]
    try:
        with pytest.raises(BadRequestError, match="EICAR_test_file"):
            await scanner.scan_file_path(source, "eicar.txt")
    finally:
        await scanner.close()


@pytest.mark.asyncio
async def test_batch_zip_extraction_does_not_invoke_parent_process_parser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processing_root = tmp_path / "processing"
    processing_root.mkdir()
    archive = processing_root / "batch.zip"
    extraction_root = processing_root / "entries"
    extraction_root.mkdir()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("folder/safe.txt", b"safe")
    monkeypatch.setattr(settings, "processing_root", str(processing_root))

    with patch.object(
        batch_zip,
        "_extract_zip_sync",
        side_effect=AssertionError("hostile ZIP reached the API process parser"),
    ):
        entries, skipped = await batch_zip._extract_zip_bounded(
            str(archive), str(extraction_root), 10
        )

    assert skipped == []
    assert [(entry.relative_path, entry.tmp_path.read_bytes()) for entry in entries] == [
        ("folder/safe.txt", b"safe")
    ]
