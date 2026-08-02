"""Adversarial ZIP, Office, ODF, and EPUB hardening regressions."""

from __future__ import annotations

import gzip
import importlib
import io
import sys
import types
import wave
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from PIL import Image

from app.config import settings
from app.core.security.file_security._zip import (
    MAX_GIF_TOTAL_PIXELS,
    _gzip_compress_path,
    _register_zip_name,
    _recompress_zip_path,
    _sanitize_embedded_image,
    _sanitize_zip_entry_name,
    _validate_zip_info,
)


def _load_office_module():
    """Load _office even in the lightweight local harness without redis installed."""

    try:
        import redis  # noqa: F401
    except ImportError:
        stub = types.ModuleType("app.core.database.redis")

        class RedisSemaphoreUnavailableError(Exception):
            pass

        @asynccontextmanager
        async def redis_semaphore(*_args, **_kwargs):
            yield

        stub.RedisSemaphoreUnavailableError = RedisSemaphoreUnavailableError
        stub.redis_client = object()
        stub.redis_semaphore = redis_semaphore
        sys.modules.setdefault("app.core.database.redis", stub)
    return importlib.import_module("app.core.security.file_security._office")


def _write_zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


def _jpeg_with_private_exif() -> bytes:
    image = Image.new("RGB", (12, 8), "red")
    exif = image.getexif()
    exif[0x010E] = "private description"
    output = io.BytesIO()
    image.save(output, format="JPEG", exif=exif)
    return output.getvalue()


def test_zip_name_unicode_and_hierarchy_conflicts() -> None:
    assert _sanitize_zip_entry_name("folder\\") == "folder/"
    assert _sanitize_zip_entry_name("．．／secret.txt") == "_/secret.txt"

    registry: dict[str, bool] = {}
    _register_zip_name(registry, "folder/", is_dir=True)
    _register_zip_name(registry, "folder/file.txt", is_dir=False)
    with pytest.raises(ValueError, match="conflicts|duplicate"):
        _register_zip_name(registry, "FOLDER", is_dir=False)


def test_encrypted_and_extreme_ratio_entries_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    encrypted = zipfile.ZipInfo("encrypted.bin")
    encrypted.flag_bits = 1
    with pytest.raises(ValueError, match="Encrypted"):
        _validate_zip_info(encrypted)

    suspicious = zipfile.ZipInfo("bomb.txt")
    suspicious.file_size = 2 * 1024 * 1024
    suspicious.compress_size = 1
    with pytest.raises(ValueError, match="compression ratio"):
        _validate_zip_info(suspicious)


def test_embedded_image_metadata_is_removed_and_format_mismatch_rejected() -> None:
    clean = _sanitize_embedded_image(_jpeg_with_private_exif(), "media/photo.jpg")
    with Image.open(io.BytesIO(clean)) as image:
        assert not image.getexif()

    with pytest.raises(ValueError, match="format mismatch"):
        _sanitize_embedded_image(_jpeg_with_private_exif(), "media/photo.png")


def test_recompressed_zip_preserves_directories_and_normalizes_timestamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "processing_root", str(tmp_path / "processing"))
    source = tmp_path / "source.zip"
    with zipfile.ZipFile(source, "w") as archive:
        directory = zipfile.ZipInfo("folder/")
        archive.writestr(directory, b"")
        archive.writestr("folder/data.txt", b"hello " * 200)
        archive.writestr("media/photo.jpg", _jpeg_with_private_exif())

    result = _recompress_zip_path(source)
    try:
        with zipfile.ZipFile(result) as archive:
            assert archive.getinfo("folder/").is_dir()
            assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())
            with Image.open(io.BytesIO(archive.read("media/photo.jpg"))) as image:
                assert not image.getexif()
    finally:
        if result != source:
            result.unlink(missing_ok=True)


def test_gzip_output_is_deterministic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "processing_root", str(tmp_path / "processing"))
    source = tmp_path / "data.txt"
    source.write_bytes(b"repeatable data" * 1000)

    first = _gzip_compress_path(source)
    first_bytes = first.read_bytes()
    first.unlink()
    second = _gzip_compress_path(source)
    second_bytes = second.read_bytes()
    second.unlink()

    assert first_bytes == second_bytes
    assert gzip.decompress(first_bytes) == source.read_bytes()


@pytest.mark.asyncio
async def test_office_package_strips_embedded_exif_and_restores_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    office = _load_office_module()
    monkeypatch.setattr(settings, "processing_root", str(tmp_path / "processing"))
    monkeypatch.setattr(settings, "environment", "development")
    source = _write_zip(
        tmp_path / "suffixless-input",
        {
            "[Content_Types].xml": b"<Types/>",
            "docProps/core.xml": b"<core/>",
            "word/document.xml": b"<document/>",
            "word/media/photo.jpg": _jpeg_with_private_exif(),
        },
    )

    result = await office._strip_ooxml_from_path(
        source,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    try:
        assert result.suffix == ".docx"
        with zipfile.ZipFile(result) as archive:
            assert "docProps/core.xml" not in archive.namelist()
            assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())
            with Image.open(io.BytesIO(archive.read("word/media/photo.jpg"))) as image:
                assert not image.getexif()
    finally:
        result.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_odf_ordering_and_script_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    office = _load_office_module()
    monkeypatch.setattr(settings, "processing_root", str(tmp_path / "processing"))
    monkeypatch.setattr(settings, "environment", "development")
    mime = "application/vnd.oasis.opendocument.text"

    valid = _write_zip(
        tmp_path / "valid.odt",
        {
            "content.xml": (
                b"<office:document-content xmlns:office='urn:test'>"
                b"<office:scripts/></office:document-content>"
            ),
            "mimetype": mime.encode(),
            "meta.xml": b"<meta/>",
        },
    )
    result = await office._strip_ooxml_from_path(valid, mime)
    try:
        with zipfile.ZipFile(result) as archive:
            assert archive.infolist()[0].filename == "mimetype"
            assert archive.infolist()[0].compress_type == zipfile.ZIP_STORED
            assert "meta.xml" not in archive.namelist()
    finally:
        result.unlink(missing_ok=True)

    malicious = _write_zip(
        tmp_path / "malicious.odt",
        {
            "mimetype": mime.encode(),
            "content.xml": b"<o:doc xmlns:o='urn:test'><o:script>evil</o:script></o:doc>",
        },
    )
    with pytest.raises(ValueError, match="script"):
        await office._strip_ooxml_from_path(malicious, mime)


@pytest.mark.asyncio
async def test_epub_manifest_drives_hidden_resource_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    office = _load_office_module()
    monkeypatch.setattr(settings, "processing_root", str(tmp_path / "processing"))
    monkeypatch.setattr(settings, "environment", "development")
    mime = "application/epub+zip"

    opf = b"""<package xmlns='http://www.idpf.org/2007/opf'>
      <metadata/><manifest>
        <item id='chapter' href='chapter.bin' media-type='application/xhtml+xml'/>
      </manifest><spine><itemref idref='chapter'/></spine>
    </package>"""
    malicious = _write_zip(
        tmp_path / "scripted.epub",
        {
            "mimetype": mime.encode(),
            "EPUB/package.opf": opf,
            "EPUB/chapter.bin": b"<html><script>alert(1)</script></html>",
        },
    )
    with pytest.raises(ValueError, match="script"):
        await office._strip_ooxml_from_path(malicious, mime)

    hidden_image_opf = b"""<package xmlns='http://www.idpf.org/2007/opf'>
      <metadata/><manifest>
        <item id='image' href='asset.bin' media-type='image/jpeg'/>
      </manifest><spine/></package>"""
    hidden_image = _write_zip(
        tmp_path / "image.epub",
        {
            "mimetype": mime.encode(),
            "EPUB/package.opf": hidden_image_opf,
            "EPUB/asset.bin": _jpeg_with_private_exif(),
        },
    )
    result = await office._strip_ooxml_from_path(hidden_image, mime)
    try:
        with zipfile.ZipFile(result) as archive:
            with Image.open(io.BytesIO(archive.read("EPUB/asset.bin"))) as image:
                assert not image.getexif()
    finally:
        result.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_epub_rejects_scripted_properties_external_css_and_broken_spine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    office = _load_office_module()
    monkeypatch.setattr(settings, "processing_root", str(tmp_path / "processing"))
    monkeypatch.setattr(settings, "environment", "development")
    mime = "application/epub+zip"

    cases = [
        (
            b"<package xmlns='http://www.idpf.org/2007/opf'><metadata/><manifest>"
            b"<item id='c' href='c.xhtml' media-type='application/xhtml+xml' properties='scripted'/>"
            b"</manifest><spine/></package>",
            {"EPUB/c.xhtml": b"<html/>"},
        ),
        (
            b"<package xmlns='http://www.idpf.org/2007/opf'><metadata/><manifest>"
            b"<item id='s' href='style.bin' media-type='text/css'/></manifest><spine/></package>",
            {"EPUB/style.bin": b"body{background:url(https://evil.invalid/pixel)}"},
        ),
        (
            b"<package xmlns='http://www.idpf.org/2007/opf'><metadata/><manifest>"
            b"<item id='c' href='c.xhtml' media-type='application/xhtml+xml'/>"
            b"</manifest><spine><itemref idref='missing'/></spine></package>",
            {"EPUB/c.xhtml": b"<html/>"},
        ),
    ]

    for index, (opf, resources) in enumerate(cases):
        entries = {"mimetype": mime.encode(), "EPUB/package.opf": opf, **resources}
        source = _write_zip(tmp_path / f"bad-{index}.epub", entries)
        with pytest.raises(ValueError):
            await office._strip_ooxml_from_path(source, mime)


@pytest.mark.asyncio
async def test_epub_declared_wav_tags_are_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mutagen.id3 import TIT2
    from mutagen.wave import WAVE

    office = _load_office_module()
    monkeypatch.setattr(settings, "processing_root", str(tmp_path / "processing"))
    monkeypatch.setattr(settings, "environment", "development")
    mime = "application/epub+zip"

    wav_path = tmp_path / "tagged.wav"
    with wave.open(str(wav_path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8_000)
        output.writeframes(b"\0\0" * 800)
    wav = WAVE(str(wav_path))
    wav.add_tags()
    wav.tags.add(TIT2(encoding=3, text="private title"))
    wav.save()

    opf = b"""<package xmlns='http://www.idpf.org/2007/opf'>
      <metadata/><manifest><item id='audio' href='audio.bin' media-type='audio/wav'/>
      </manifest><spine/></package>"""
    source = _write_zip(
        tmp_path / "audio.epub",
        {
            "mimetype": mime.encode(),
            "EPUB/package.opf": opf,
            "EPUB/audio.bin": wav_path.read_bytes(),
        },
    )
    result = await office._strip_ooxml_from_path(source, mime)
    try:
        extracted = tmp_path / "clean.wav"
        with zipfile.ZipFile(result) as archive:
            extracted.write_bytes(archive.read("EPUB/audio.bin"))
        assert WAVE(str(extracted)).tags in (None, {})
    finally:
        result.unlink(missing_ok=True)
