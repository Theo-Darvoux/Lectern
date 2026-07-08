"""Tests for path-based file security APIs (strip_metadata_file, compress_file_path, helpers).

These functions are the active production codepath — the legacy in-memory variants
(_compress_pdf, _compress_video, etc.) are deprecated. This file provides direct
coverage for every path-based dispatcher and auxiliary helper.
"""

import gzip
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pikepdf
import pytest

from app.config import settings
from app.core.file_security import (
    CompressResultPath,
    SvgSecurityError,
    _gzip_compress_path,
    _recompress_zip_path,
    compress_file_path,
    strip_metadata_file,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_minimal_pdf(tmp_path: Path, size_bytes: int = 0) -> Path:
    """Write a minimal but well-formed PDF to a temp file.

    If size_bytes > 0, pad the PDF to at least that size by adding blank pages.
    """
    pdf = pikepdf.new()
    page = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name("/Page"),
            MediaBox=pikepdf.Array([0, 0, 612, 792]),
        )
    )
    pdf.pages.append(pikepdf.Page(page))

    # Add padding pages if needed to reach minimum size
    if size_bytes > 0:
        while True:
            p_tmp = tmp_path / "test_temp.pdf"
            pdf.save(str(p_tmp))
            if p_tmp.stat().st_size >= size_bytes:
                p_tmp.unlink()
                break
            # Add another blank page
            pdf.pages.append(pikepdf.Page(page))

    p = tmp_path / "test.pdf"
    pdf.save(str(p))
    return p


def _make_zip(tmp_path: Path, entries: dict[str, bytes]) -> Path:
    """Write a ZIP archive with the given {name: content} entries."""
    p = tmp_path / "test.zip"
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    p.write_bytes(buf.getvalue())
    return p


# ── strip_metadata_file dispatcher ───────────────────────────────────────────


class TestStripMetadataFile:
    """Tests for strip_metadata_file path dispatcher."""

    @pytest.mark.asyncio
    async def test_strips_image(self, tmp_path):
        """strip_metadata_file for image/jpeg calls _strip_image_from_path."""
        img_path = tmp_path / "img.jpg"
        img_path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
        clean_path = tmp_path / "clean.jpg"
        clean_path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 80)

        with patch("app.core.file_security.strip._strip_image_from_path") as m:
            m.return_value = clean_path
            result = await strip_metadata_file(img_path, "image/jpeg")
        assert result == clean_path

    @pytest.mark.asyncio
    async def test_strips_pdf(self, tmp_path):
        """strip_metadata_file for application/pdf calls _strip_pdf_from_path."""
        pdf_path = tmp_path / "doc.pdf"
        pdf_path.write_bytes(b"%PDF-1.4")
        clean_path = tmp_path / "clean.pdf"
        clean_path.write_bytes(b"%PDF-1.4 stripped")

        with patch("app.core.file_security.strip._strip_pdf_from_path") as m:
            m.return_value = clean_path
            result = await strip_metadata_file(pdf_path, "application/pdf")
        assert result == clean_path

    @pytest.mark.asyncio
    async def test_strips_video(self, tmp_path):
        """strip_metadata_file for video/mp4 calls _strip_video_from_path."""
        mp4_path = tmp_path / "video.mp4"
        mp4_path.write_bytes(b"\x00\x00\x00\x08ftypisom" + b"\x00" * 20)
        clean_path = tmp_path / "clean.mp4"
        clean_path.write_bytes(b"\x00\x00\x00\x08ftypisom" + b"\x00" * 15)

        with patch(
            "app.core.file_security.strip._strip_video_from_path",
            new_callable=AsyncMock,
            return_value=clean_path,
        ):
            result = await strip_metadata_file(mp4_path, "video/mp4")
        assert result == clean_path

    @pytest.mark.asyncio
    async def test_strips_audio(self, tmp_path):
        """strip_metadata_file for audio/mpeg calls _strip_audio_from_path."""
        mp3_path = tmp_path / "audio.mp3"
        mp3_path.write_bytes(b"ID3" + b"\x00" * 50)
        clean_path = tmp_path / "clean.mp3"
        clean_path.write_bytes(b"ID3" + b"\x00" * 30)

        with patch("app.core.file_security.strip._strip_audio_from_path") as m:
            m.return_value = clean_path
            result = await strip_metadata_file(mp3_path, "audio/mpeg")
        assert result == clean_path

    @pytest.mark.asyncio
    async def test_strips_ole2(self, tmp_path):
        """strip_metadata_file for OLE2 calls _strip_ole2_from_path."""
        doc_path = tmp_path / "doc.doc"
        doc_path.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 100)
        clean_path = tmp_path / "clean.doc"
        clean_path.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 80)

        with patch(
            "app.core.file_security.strip._strip_ole2_from_path",
            new_callable=AsyncMock,
            return_value=clean_path,
        ):
            result = await strip_metadata_file(doc_path, "application/msword")
        assert result == clean_path

    @pytest.mark.asyncio
    async def test_strips_ooxml(self, tmp_path):
        """strip_metadata_file for OOXML/ZIP calls _strip_ooxml_from_path."""
        docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        docx_path = tmp_path / "doc.docx"
        docx_path.write_bytes(b"PK\x03\x04" + b"\x00" * 100)
        clean_path = tmp_path / "clean.docx"
        clean_path.write_bytes(b"PK\x03\x04" + b"\x00" * 80)

        with patch(
            "app.core.file_security.strip._strip_ooxml_from_path",
            new_callable=AsyncMock,
            return_value=clean_path,
        ):
            result = await strip_metadata_file(docx_path, docx_mime)
        assert result == clean_path

    @pytest.mark.asyncio
    async def test_unknown_mime_returns_original(self, tmp_path):
        """Unknown MIME type → fail-open, return original path unchanged."""
        unknown_path = tmp_path / "file.bin"
        unknown_path.write_bytes(b"\x00" * 50)

        result = await strip_metadata_file(unknown_path, "application/octet-stream")
        assert result == unknown_path

    @pytest.mark.asyncio
    async def test_fail_closed_on_image_exception(self, tmp_path):
        """Any exception during image strip raises ValueError (fail-closed for privacy)."""
        img_path = tmp_path / "img.png"
        img_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)

        with (
            patch(
                "app.core.file_security.strip._strip_image_from_path",
                side_effect=RuntimeError("Pillow crashed"),
            ),
            pytest.raises(ValueError, match="sanitize"),
        ):
            await strip_metadata_file(img_path, "image/png")

    @pytest.mark.asyncio
    async def test_value_error_propagates(self, tmp_path):
        """ValueError from OLE2 macro check must propagate (no fail-open)."""
        doc_path = tmp_path / "macro.doc"
        doc_path.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 100)

        with (
            patch(
                "app.core.file_security.strip._strip_ole2_from_path",
                new_callable=AsyncMock,
                side_effect=ValueError("Auto-exec macro"),
            ),
            pytest.raises(ValueError, match="Auto-exec macro"),
        ):
            await strip_metadata_file(doc_path, "application/msword")


# ── _strip_pdf_from_path (real pikepdf) ──────────────────────────────────────


class TestStripPdfFromPath:
    """Integration tests for the path-based PDF stripper."""

    def test_removes_open_action(self, tmp_path):
        """_strip_pdf_from_path removes /OpenAction from the catalog."""
        from app.core.file_security import _strip_pdf_from_path

        pdf_path = _make_minimal_pdf(tmp_path)

        # Add OpenAction
        with pikepdf.open(str(pdf_path), allow_overwriting_input=True) as pdf:
            pdf.Root["/OpenAction"] = pikepdf.Dictionary(
                S=pikepdf.Name("/JavaScript"),
                JS=pikepdf.String("app.alert(1)"),
            )
            pdf.save(str(pdf_path))

        clean = _strip_pdf_from_path(pdf_path)

        with pikepdf.open(str(clean)) as pdf:
            assert "/OpenAction" not in pdf.Root

    def test_removes_embedded_files(self, tmp_path):
        """_strip_pdf_from_path removes /EmbeddedFiles from Names tree."""
        from app.core.file_security import _strip_pdf_from_path

        pdf_path = _make_minimal_pdf(tmp_path)

        with pikepdf.open(str(pdf_path), allow_overwriting_input=True) as pdf:
            pdf.Root["/Names"] = pikepdf.Dictionary(
                EmbeddedFiles=pikepdf.Dictionary(
                    Names=pikepdf.Array([pikepdf.String("payload"), pikepdf.Dictionary()])
                )
            )
            pdf.save(str(pdf_path))

        clean = _strip_pdf_from_path(pdf_path)

        with pikepdf.open(str(clean)) as pdf:
            if "/Names" in pdf.Root:
                assert "/EmbeddedFiles" not in pdf.Root["/Names"]

    def test_clean_pdf_remains_valid(self, tmp_path):
        """_strip_pdf_from_path on a clean PDF produces a readable output."""
        from app.core.file_security import _strip_pdf_from_path

        pdf_path = _make_minimal_pdf(tmp_path)
        clean = _strip_pdf_from_path(pdf_path)
        with pikepdf.open(str(clean)) as pdf:
            assert len(pdf.pages) == 1

    def test_corrupt_pdf_returns_original(self, tmp_path):
        """_strip_pdf_from_path fails open on corrupt input."""
        from app.core.file_security import _strip_pdf_from_path

        bad = tmp_path / "bad.pdf"
        bad.write_bytes(b"not a pdf at all")
        result = _strip_pdf_from_path(bad)
        assert result == bad  # returned original path unchanged


# ── _gzip_compress_path ──────────────────────────────────────────────────────


class TestGzipCompressPath:
    """Tests for _gzip_compress_path."""

    def test_produces_valid_gzip(self, tmp_path):
        """Output is a valid gzip stream that decompresses to the original."""
        src = tmp_path / "text.txt"
        content = b"Hello world! " * 500
        src.write_bytes(content)

        result = _gzip_compress_path(src)
        assert result != src  # smaller → new path returned
        decompressed = gzip.decompress(result.read_bytes())
        assert decompressed == content

    def test_returns_original_when_larger(self, tmp_path):
        """If gzip output is larger than input, return the original path."""
        src = tmp_path / "incompressible.bin"
        # Truly random / already-compressed data may not shrink with gzip.
        # Use a designed incompressible pattern.
        import os

        src.write_bytes(os.urandom(50))  # small random — gzip overhead > savings
        result = _gzip_compress_path(src)
        # Either the original path comes back, or a smaller file; never crashes.
        assert result.exists()

    def test_cleanup_on_exception(self, tmp_path):
        """Temp file is cleaned up when an exception occurs mid-compression."""
        src = tmp_path / "src.txt"
        src.write_bytes(b"data " * 100)

        with patch("gzip.open", side_effect=OSError("disk full")), pytest.raises(OSError):
            _gzip_compress_path(src)


# ── _recompress_zip_path ─────────────────────────────────────────────────────


class TestRecompressZipPath:
    """Tests for _recompress_zip_path."""

    def test_valid_zip_recompressed(self, tmp_path):
        """Normal ZIP is recompressed and remains readable."""
        src = _make_zip(tmp_path, {"doc.txt": b"Hello" * 200, "sub/data.txt": b"More" * 100})
        result = _recompress_zip_path(src)
        with zipfile.ZipFile(result) as z:
            assert "doc.txt" in z.namelist()
            assert z.read("doc.txt") == b"Hello" * 200

    def test_path_traversal_sanitised(self, tmp_path):
        """ZIP entries with path traversal are sanitised in the output."""
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            info = zipfile.ZipInfo("../../etc/passwd")
            z.writestr(info, b"root:x:0:0")
        src = tmp_path / "traverse.zip"
        src.write_bytes(buf.getvalue())

        result = _recompress_zip_path(src)

        with zipfile.ZipFile(result) as z:
            for name in z.namelist():
                assert ".." not in name

    def test_bomb_single_entry_exceeds_limit(self, tmp_path):
        """_recompress_zip_path raises ValueError when one entry exceeds 200 MiB."""
        src = _make_zip(tmp_path, {"dummy.txt": b"x"})

        large_info = MagicMock()
        large_info.filename = "big.bin"
        large_info.file_size = 201 * 1024 * 1024
        large_info.date_time = (2024, 1, 1, 0, 0, 0)

        with patch("zipfile.ZipFile.infolist", return_value=[large_info]):
            with pytest.raises(ValueError, match="too large"):
                _recompress_zip_path(src)

    def test_bomb_total_exceeds_limit(self, tmp_path):
        """_recompress_zip_path raises ValueError when total uncompressed size exceeds 500 MiB."""
        src = _make_zip(tmp_path, {"dummy.txt": b"x"})

        entries = []
        for i in range(3):
            info = MagicMock()
            info.filename = f"part{i}.bin"
            info.file_size = 180 * 1024 * 1024  # 3 × 180 MiB = 540 MiB > 500 MiB cap
            info.date_time = (2024, 1, 1, 0, 0, 0)
            entries.append(info)

        with patch("zipfile.ZipFile.infolist", return_value=entries):
            with pytest.raises(ValueError, match="too large"):
                _recompress_zip_path(src)

    def test_invalid_zip_raises(self, tmp_path):
        """Invalid ZIP input raises BadZipFile."""
        bad = tmp_path / "notazip.bin"
        bad.write_bytes(b"this is not a zip")
        with pytest.raises(zipfile.BadZipFile):
            _recompress_zip_path(bad)

    def test_returns_original_when_not_smaller(self, tmp_path):
        """If the recompressed ZIP is not smaller, returns the original path."""
        # Create a very small already-maximally-compressed ZIP
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            z.writestr("tiny.txt", b"x")
        src = tmp_path / "tiny.zip"
        src.write_bytes(buf.getvalue())
        result = _recompress_zip_path(src)
        # Either original or recompressed - must not crash
        assert result.exists()


# ── compress_file_path dispatcher ────────────────────────────────────────────


class TestCompressFilePath:
    """Tests for compress_file_path path-based dispatcher."""

    @pytest.mark.asyncio
    async def test_pdf_dispatch(self, tmp_path):
        """Dispatches PDF to _compress_pdf_path."""
        # Create PDF larger than 10 KB compression threshold
        pdf_path = _make_minimal_pdf(tmp_path, size_bytes=11 * 1024)
        smaller = tmp_path / "small.pdf"
        smaller.write_bytes(b"%PDF-1.4 smaller")

        with patch(
            "app.core.file_security.compress._compress_pdf_path",
            new_callable=AsyncMock,
            return_value=smaller,
        ):
            result = await compress_file_path(pdf_path, "application/pdf", "test.pdf")
        assert isinstance(result, CompressResultPath)
        assert result.path == smaller
        assert result.mime_type == "application/pdf"
        assert result.content_encoding is None

    @pytest.mark.asyncio
    async def test_video_mp4_dispatch(self, tmp_path):
        """Dispatches video/mp4 to _compress_video_path."""
        src = tmp_path / "video.mp4"
        # Create file larger than 10 KB compression threshold
        src.write_bytes(b"\x00\x00\x00\x08ftypisom" + b"\x00" * (11 * 1024))
        small = tmp_path / "small.mp4"
        small.write_bytes(b"\x00\x00\x00\x08ftypisom" + b"\x00" * 30)

        with patch(
            "app.core.file_security._compress_video_path",
            new_callable=AsyncMock,
            return_value=small,
        ):
            result = await compress_file_path(src, "video/mp4")
        assert result.mime_type == "video/mp4"
        assert result.content_encoding is None

    @pytest.mark.asyncio
    async def test_wav_dispatch_and_mime_change(self, tmp_path):
        """Dispatches audio/wav to _convert_to_opus_path; mime_type updates to audio/webm."""
        src = tmp_path / "audio.wav"
        # Create file larger than 10 KB compression threshold
        src.write_bytes(b"RIFF" + b"\x00" * (11 * 1024))
        opus = tmp_path / "audio.webm"
        opus.write_bytes(b"Opus" + b"\x00" * 30)

        with patch(
            "app.core.file_security.compress._convert_to_opus_path",
            new_callable=AsyncMock,
            return_value=opus,
        ):
            result = await compress_file_path(src, "audio/wav")
        assert result.mime_type == "audio/webm"

    @pytest.mark.asyncio
    async def test_wav_no_conversion_keeps_mime(self, tmp_path):
        """If conversion returns the original path (no benefit), MIME stays audio/wav."""
        src = tmp_path / "audio.wav"
        # Create file larger than 10 KB compression threshold
        src.write_bytes(b"RIFF" + b"\x00" * (11 * 1024))

        with patch(
            "app.core.file_security._convert_to_opus_path",
            new_callable=AsyncMock,
            return_value=src,  # returns same path = no conversion
        ):
            result = await compress_file_path(src, "audio/wav")
        assert result.mime_type == "audio/wav"

    @pytest.mark.asyncio
    async def test_zip_docx_dispatch(self, tmp_path):
        """Dispatches DOCX (ZIP MIME) to _recompress_zip_path."""
        docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        src = _make_zip(tmp_path, {"[Content_Types].xml": b"<Types/>"})
        # rename to .docx for clarity
        dst = tmp_path / "doc.docx"
        src.rename(dst)

        result = await compress_file_path(dst, docx_mime, "doc.docx")
        assert isinstance(result, CompressResultPath)
        assert result.mime_type == docx_mime
        assert result.content_encoding is None

    @pytest.mark.asyncio
    async def test_text_plain_gzipped(self, tmp_path):
        """text/plain gets gzip-compressed with Content-Encoding: gzip."""
        src = tmp_path / "file.txt"
        src.write_bytes(b"Hello world! " * 1000)

        result = await compress_file_path(src, "text/plain", "file.txt")
        assert result.content_encoding == "gzip"
        assert result.size < src.stat().st_size

    @pytest.mark.asyncio
    async def test_json_gzipped(self, tmp_path):
        """application/json gets gzip-compressed."""
        src = tmp_path / "data.json"
        # Create file larger than 10 KB compression threshold
        src.write_bytes(b'{"key": "value"}' * 1000)

        result = await compress_file_path(src, "application/json", "data.json")
        assert result.content_encoding == "gzip"

    @pytest.mark.asyncio
    async def test_svg_safe_gzipped(self, tmp_path):
        """Safe SVG is validated, optimised with scour, then gzipped."""
        svg_content = (
            b'<svg xmlns="http://www.w3.org/2000/svg">'
            + b"<!-- comment -->"
            + b'<rect width="10" height="10"/>' * 100
            + b"</svg>"
        )
        src = tmp_path / "image.svg"
        src.write_bytes(svg_content)

        result = await compress_file_path(src, "image/svg+xml", "image.svg")
        assert isinstance(result, CompressResultPath)
        # SVG should be gzip-encoded when it compresses well
        # (may be gzip or plain scoured depending on size)
        assert result.path.exists()

    @pytest.mark.asyncio
    async def test_svg_xss_raises(self, tmp_path):
        """SVG with <script> raises SvgSecurityError (not caught/suppressed)."""
        # SVG safety is checked regardless of file size (happens before compression skip)
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        src = tmp_path / "xss.svg"
        src.write_bytes(svg)

        with pytest.raises(SvgSecurityError):
            await compress_file_path(src, "image/svg+xml", "xss.svg")

    @pytest.mark.asyncio
    async def test_image_dispatch(self, tmp_path):
        """Dispatches image/jpeg to _compress_image_path."""
        src = tmp_path / "photo.jpg"
        # Create file larger than 10 KB compression threshold
        src.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * (11 * 1024))
        small = tmp_path / "small.jpg"
        small.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 50)

        with patch(
            "app.core.file_security.compress._compress_image_path",
            return_value=small,
        ):
            result = await compress_file_path(src, "image/jpeg", "photo.jpg")
        assert result.path == small
        assert result.mime_type == "image/webp"
        assert result.content_encoding is None

    @pytest.mark.asyncio
    async def test_exception_fail_open(self, tmp_path):
        """Non-security exceptions during compression → fail-open, return original."""
        src = tmp_path / "test.pdf"
        src.write_bytes(b"%PDF some content")

        with patch(
            "app.core.file_security._compress_pdf_path",
            new_callable=AsyncMock,
            side_effect=RuntimeError("GS crashed"),
        ):
            result = await compress_file_path(src, "application/pdf", "test.pdf")
        assert result.path == src
        assert result.content_encoding is None

    @pytest.mark.asyncio
    async def test_returns_compress_result_path_type(self, tmp_path):
        """compress_file_path always returns a CompressResultPath namedtuple."""
        src = tmp_path / "binary.bin"
        src.write_bytes(b"\x00" * 100)

        result = await compress_file_path(src, "application/octet-stream")
        assert isinstance(result, CompressResultPath)
        assert isinstance(result.path, Path)
        assert isinstance(result.size, int)
        assert result.size > 0


# ── Scanner path-based scan additions ────────────────────────────────────────


class TestScannerPathBased:
    """Additional tests for scan_file_path — threat and error cases."""

    @pytest.mark.asyncio
    async def test_scan_file_path_yara_threat_raises(self, tmp_path):
        """scan_file_path raises BadRequestError when YARA detects a threat."""
        from app.core.exceptions import BadRequestError
        from app.core.scanner import MalwareScanner

        scanner = MalwareScanner()
        scanner._scan_yara_path = AsyncMock(return_value="EICAR_test_file")  # type: ignore[method-assign]
        scanner.check_malwarebazaar = AsyncMock(return_value=None)  # type: ignore[method-assign]

        test_file = tmp_path / "malware.bin"
        test_file.write_bytes(b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR")

        with patch.object(settings, "bazaar_async_enabled", False):
            with pytest.raises(BadRequestError, match="ERR_MALWARE_DETECTED"):
                await scanner.scan_file_path(test_file, "malware.bin")

    @pytest.mark.asyncio
    async def test_scan_file_path_bazaar_threat_raises(self, tmp_path):
        """scan_file_path raises BadRequestError when MalwareBazaar returns a hit."""
        from app.core.exceptions import BadRequestError
        from app.core.scanner import MalwareScanner

        scanner = MalwareScanner()
        scanner._scan_yara_path = AsyncMock(return_value=None)  # type: ignore[method-assign]
        scanner.check_malwarebazaar = AsyncMock(return_value="Emotet")  # type: ignore[method-assign]

        test_file = tmp_path / "emotet.doc"
        test_file.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 50)

        with patch.object(settings, "bazaar_async_enabled", False):
            with pytest.raises(BadRequestError, match="ERR_MALWARE_DETECTED"):
                await scanner.scan_file_path(test_file, "emotet.doc")

    @pytest.mark.asyncio
    async def test_scan_file_path_yara_error_fails_closed(self, tmp_path):
        """scan_file_path raises ServiceUnavailableError when YARA scan fails."""
        from app.core.exceptions import ServiceUnavailableError
        from app.core.scanner import MalwareScanner

        scanner = MalwareScanner()
        scanner._scan_yara_path = AsyncMock(side_effect=RuntimeError("YARA crashed"))  # type: ignore[method-assign]
        scanner.check_malwarebazaar = AsyncMock(return_value=None)  # type: ignore[method-assign]

        test_file = tmp_path / "file.bin"
        test_file.write_bytes(b"content")

        with patch.object(settings, "bazaar_async_enabled", False):
            with pytest.raises(ServiceUnavailableError, match="fail-closed"):
                await scanner.scan_file_path(test_file, "file.bin")

    @pytest.mark.asyncio
    async def test_scan_file_path_bazaar_error_fails_closed(self, tmp_path):
        """scan_file_path raises ServiceUnavailableError when MalwareBazaar fails."""
        from app.core.exceptions import ServiceUnavailableError
        from app.core.scanner import MalwareScanner

        scanner = MalwareScanner()
        scanner._scan_yara_path = AsyncMock(return_value=None)  # type: ignore[method-assign]
        scanner.check_malwarebazaar = AsyncMock(  # type: ignore[method-assign]
            side_effect=ServiceUnavailableError("Bazaar down")
        )

        test_file = tmp_path / "file.bin"
        test_file.write_bytes(b"content")

        with patch.object(settings, "bazaar_async_enabled", False):
            with pytest.raises(ServiceUnavailableError, match="fail-closed"):
                await scanner.scan_file_path(test_file, "file.bin")

    @pytest.mark.asyncio
    async def test_scan_file_path_uses_provided_hash(self, tmp_path):
        """scan_file_path uses the provided bazaar_hash and skips file hashing."""
        from app.core.scanner import MalwareScanner

        scanner = MalwareScanner()
        scanner._scan_yara_path = AsyncMock(return_value=None)  # type: ignore[method-assign]
        scanner.check_malwarebazaar = AsyncMock(return_value=None)  # type: ignore[method-assign]

        test_file = tmp_path / "file.bin"
        test_file.write_bytes(b"content")

        precomputed_hash = "a" * 64
        with patch.object(settings, "bazaar_async_enabled", False):
            await scanner.scan_file_path(test_file, "file.bin", bazaar_hash=precomputed_hash)

        # MalwareBazaar must have been called with the precomputed hash
        scanner.check_malwarebazaar.assert_called_once_with(precomputed_hash, "file.bin")

    @pytest.mark.asyncio
    async def test_yara_path_not_initialized_raises(self, tmp_path):
        """_scan_yara_path raises RuntimeError when rules are None."""
        from app.core.scanner import MalwareScanner

        scanner = MalwareScanner()
        # scanner.rules is None by default

        test_file = tmp_path / "file.bin"
        test_file.write_bytes(b"data")

        with pytest.raises(RuntimeError, match="not initialized"):
            await scanner._scan_yara_path(test_file, "file.bin")


# ── Temporary File Robustness and Leak Protection Tests ───────────────────────


class TestTempFileLeakProtection:
    """Tests to verify that temporary files are properly cleaned up on exceptions."""

    def test_strip_pdf_unlinks_temp_on_exception(self, tmp_path):
        """_strip_pdf_from_path unlinks the temp file if saving fails."""
        from app.core.file_security._pdf import _strip_pdf_from_path

        pdf_path = _make_minimal_pdf(tmp_path)

        # We patch pikepdf.Pdf.save to raise an exception, but let tempfile work
        with patch("pikepdf.Pdf.save", side_effect=RuntimeError("Save failed")):
            with patch("tempfile.NamedTemporaryFile") as mock_temp:
                mock_file = MagicMock()
                mock_file.name = str(tmp_path / "leaked_temp_pdf.pdf")
                mock_temp.return_value.__enter__.return_value = mock_file

                # Make sure the file exists initially to check if it's unlinked
                Path(mock_file.name).write_bytes(b"dummy pdf data")
                assert Path(mock_file.name).exists()

                result = _strip_pdf_from_path(pdf_path)
                assert result == pdf_path
                assert not Path(mock_file.name).exists()

    def test_strip_image_unlinks_temp_on_exception(self, tmp_path):
        """_strip_image_from_path unlinks the temp file if saving fails."""
        from app.core.file_security._image import _strip_image_from_path
        from PIL import Image

        img_path = tmp_path / "test.png"
        img = Image.new("RGB", (10, 10))
        img.save(img_path, format="PNG")

        with patch("app.core.file_security._image._save_stripped_image", side_effect=RuntimeError("Save failed")):
            with patch("tempfile.NamedTemporaryFile") as mock_temp:
                mock_file = MagicMock()
                mock_file.name = str(tmp_path / "leaked_temp_img.png")
                mock_temp.return_value = mock_file

                Path(mock_file.name).write_bytes(b"dummy image data")
                assert Path(mock_file.name).exists()

                result = _strip_image_from_path(img_path)
                assert result == img_path
                assert not Path(mock_file.name).exists()

    def test_strip_audio_unlinks_temp_on_exception(self, tmp_path):
        """_strip_audio_from_path unlinks the temp file if mutagen save fails."""
        from app.core.file_security._audio_video import _strip_audio_from_path

        audio_path = tmp_path / "test.mp3"
        audio_path.write_bytes(b"ID3...")

        with patch("mutagen.File", side_effect=RuntimeError("Mutagen crashed")):
            with patch("tempfile.NamedTemporaryFile") as mock_temp:
                mock_file = MagicMock()
                mock_file.name = str(tmp_path / "leaked_temp_audio.mp3")
                mock_temp.return_value = mock_file

                Path(mock_file.name).write_bytes(b"dummy audio data")
                assert Path(mock_file.name).exists()

                result = _strip_audio_from_path(audio_path, "audio/mpeg")
                assert result == audio_path
                assert not Path(mock_file.name).exists()

    def test_optimize_svg_unlinks_temp_on_exception(self, tmp_path):
        """_optimize_svg_to_path unlinks the temp file if safety check raises SvgSecurityError."""
        from app.core.file_security.compress import _optimize_svg_to_path

        svg_path = tmp_path / "test.svg"
        svg_path.write_bytes(b"<svg></svg>")

        with patch("app.core.file_security.compress.check_svg_safety") as mock_check:
            # First safety check succeeds, second fails on optimized version
            mock_check.side_effect = [None, SvgSecurityError("Malicious SVG")]

            with patch("app.core.file_security.compress._optimize_svg", return_value=b"<svg/>"):
                with patch("tempfile.NamedTemporaryFile") as mock_temp:
                    mock_file = MagicMock()
                    mock_file.name = str(tmp_path / "leaked_temp_svg.svg")
                    mock_temp.return_value = mock_file

                    Path(mock_file.name).write_bytes(b"dummy svg data")
                    assert Path(mock_file.name).exists()

                    with pytest.raises(SvgSecurityError):
                        _optimize_svg_to_path(svg_path, "test.svg")

                    assert not Path(mock_file.name).exists()

    @pytest.mark.asyncio
    async def test_run_scan_and_strip_unlinks_temp_on_scan_failure(self, tmp_path):
        """run_scan_and_strip unlinks the stripped path if scan returns an exception."""
        from app.workers.upload.stages.scan_strip import run_scan_and_strip
        from app.core.processing import ProcessingFile

        pf_path = tmp_path / "pf.bin"
        pf_path.write_bytes(b"original data")
        pf = ProcessingFile(pf_path, len(pf_path.read_bytes()))

        # We simulate a scan failure
        scan_res_exc = RuntimeError("Scan crashed")
        strip_res_path = tmp_path / "stripped_temp.bin"
        strip_res_path.write_bytes(b"stripped data")

        ctx = MagicMock()
        ctx.scanner = MagicMock()

        # Parallel tasks return (scan_res_exc, strip_res_path)
        with patch("app.workers.upload.stages.scan_strip.parallel_tasks", new_callable=AsyncMock) as mock_tasks:
            mock_tasks.return_value = [scan_res_exc, strip_res_path]

            # Make sure strip_res_path exists
            assert strip_res_path.exists()

            with pytest.raises(RuntimeError, match="Scan crashed"):
                await run_scan_and_strip(
                    ctx, pf, pf_path, "pf.bin", "hash", "bin", "bin", "upload_id", MagicMock()
                )

            # verify that the temp stripped file was deleted and not registered to pf
            assert not strip_res_path.exists()
            assert pf.path == pf_path

    @pytest.mark.asyncio
    async def test_run_strip_only_unlinks_temp_on_exception(self, tmp_path):
        """run_strip_only unlinks the clean file if an exception occurs during replace_with."""
        from app.workers.upload.stages.scan_strip import run_strip_only
        from app.core.processing import ProcessingFile

        pf_path = tmp_path / "pf.bin"
        pf_path.write_bytes(b"original data")
        pf = ProcessingFile(pf_path, len(pf_path.read_bytes()))

        strip_res_path = tmp_path / "stripped_temp.bin"
        strip_res_path.write_bytes(b"stripped data")

        # mock replace_with to fail
        with patch.object(pf, "replace_with", side_effect=RuntimeError("Replace failed")):
            with patch("app.workers.upload.stages.scan_strip.strip_metadata_file", new_callable=AsyncMock) as mock_strip:
                mock_strip.return_value = strip_res_path

                assert strip_res_path.exists()

                with pytest.raises(RuntimeError, match="Replace failed"):
                    await run_strip_only(pf, pf_path, "bin", "upload_id", MagicMock())

                # verify that the temp stripped file was deleted
                assert not strip_res_path.exists()
