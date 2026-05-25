"""Tests for core/mimetypes.py — MIME detection and registry."""

from app.core.mimetypes import (
    ALLOWED_EXTENSIONS,
    MimeRegistry,
    guess_mime_from_bytes,
)

# ── guess_mime_from_bytes ────────────────────────────────────────────────────


class TestGuessMimeFromBytes:
    """Unit tests for magic-byte MIME detection."""

    def test_pdf(self):
        assert guess_mime_from_bytes(b"%PDF-1.4 test") == "application/pdf"

    def test_png(self):
        assert guess_mime_from_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100) == "image/png"

    def test_jpeg(self):
        assert guess_mime_from_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 20) == "image/jpeg"

    def test_gif87a(self):
        assert guess_mime_from_bytes(b"GIF87a" + b"\x00" * 20) == "image/gif"

    def test_gif89a(self):
        assert guess_mime_from_bytes(b"GIF89a" + b"\x00" * 20) == "image/gif"

    def test_webp(self):
        data = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 20
        assert guess_mime_from_bytes(data) == "image/webp"

    def test_svg_lower(self):
        assert guess_mime_from_bytes(b"<svg xmlns=") == "image/svg+xml"

    def test_svg_mixed_case(self):
        # The check is done on data.lower(), so mixed case must work
        assert guess_mime_from_bytes(b"<?xml version='1.0'?><SVG>") == "image/svg+xml"

    def test_djvu(self):
        assert guess_mime_from_bytes(b"AT&TFORM" + b"\x00" * 20) == "image/vnd.djvu"

    def test_mp3_id3(self):
        assert guess_mime_from_bytes(b"ID3" + b"\x00" * 20) == "audio/mpeg"

    def test_mp3_sync_fb(self):
        assert guess_mime_from_bytes(b"\xff\xfb" + b"\x00" * 20) == "audio/mpeg"

    def test_mp3_sync_f3(self):
        assert guess_mime_from_bytes(b"\xff\xf3" + b"\x00" * 20) == "audio/mpeg"

    def test_flac(self):
        assert guess_mime_from_bytes(b"fLaC" + b"\x00" * 20) == "audio/flac"

    def test_ogg(self):
        assert guess_mime_from_bytes(b"OggS" + b"\x00" * 20) == "audio/ogg"

    def test_wav(self):
        data = b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 20
        assert guess_mime_from_bytes(data) == "audio/wav"

    def test_mp4_ftyp(self):
        # ftyp box at offset 4, non-M4A brand → video/mp4
        data = b"\x00\x00\x00\x08ftyp" + b"isom" + b"\x00" * 20
        assert guess_mime_from_bytes(data) == "video/mp4"

    def test_m4a(self):
        # ftyp box at offset 4, M4A brand → audio/mp4
        data = b"\x00\x00\x00\x08ftypM4A " + b"\x00" * 20
        assert guess_mime_from_bytes(data) == "audio/mp4"

    def test_epub(self):
        # EPUB: PK magic + mimetype header in first 200 bytes
        data = b"PK\x03\x04" + b"\x00" * 26 + b"mimetypeapplication/epub+zip" + b"\x00" * 100
        assert guess_mime_from_bytes(data) == "application/epub+zip"

    def test_odt(self):
        data = (
            b"PK\x03\x04"
            + b"\x00" * 26
            + b"mimetypeapplication/vnd.oasis.opendocument.text"
            + b"\x00" * 100
        )
        assert guess_mime_from_bytes(data) == "application/vnd.oasis.opendocument.text"

    def test_ods(self):
        data = (
            b"PK\x03\x04"
            + b"\x00" * 26
            + b"mimetypeapplication/vnd.oasis.opendocument.spreadsheet"
            + b"\x00" * 100
        )
        assert guess_mime_from_bytes(data) == "application/vnd.oasis.opendocument.spreadsheet"

    def test_docx_ooxml(self):
        # DOCX: PK magic + "word/" in first 2048 bytes
        data = b"PK\x03\x04" + b"\x00" * 196 + b"word/document.xml" + b"\x00" * 1800
        result = guess_mime_from_bytes(data)
        assert result == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    def test_xlsx_ooxml(self):
        data = b"PK\x03\x04" + b"\x00" * 196 + b"xl/workbook.xml" + b"\x00" * 1800
        result = guess_mime_from_bytes(data)
        assert result == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    def test_pptx_ooxml(self):
        data = b"PK\x03\x04" + b"\x00" * 196 + b"ppt/presentation.xml" + b"\x00" * 1800
        result = guess_mime_from_bytes(data)
        assert result == "application/vnd.openxmlformats-officedocument.presentationml.presentation"

    def test_ole2_default(self):
        # OLE2 magic without type inference → returns default
        data = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 20
        assert guess_mime_from_bytes(data) == "application/octet-stream"

    def test_too_short_returns_default(self):
        assert guess_mime_from_bytes(b"\xff\xd8") == "application/octet-stream"
        assert guess_mime_from_bytes(b"") == "application/octet-stream"

    def test_truly_unknown(self):
        assert guess_mime_from_bytes(b"\x00\x01\x02\x03\x04garbage") == "application/octet-stream"

    def test_custom_default(self):
        result = guess_mime_from_bytes(b"\x00\x01\x02\x03", default="application/binary")
        assert result == "application/binary"


# ── MimeRegistry ─────────────────────────────────────────────────────────────


class TestMimeRegistry:
    """Unit tests for MimeRegistry static methods."""

    def test_is_supported_extension_pdf(self):
        assert MimeRegistry.is_supported_extension(".pdf") is True

    def test_is_supported_extension_svg(self):
        assert MimeRegistry.is_supported_extension(".svg") is True

    def test_is_supported_extension_py(self):
        assert MimeRegistry.is_supported_extension(".py") is True

    def test_is_supported_extension_exe_rejected(self):
        assert MimeRegistry.is_supported_extension(".exe") is False

    def test_is_supported_extension_empty_rejected(self):
        assert MimeRegistry.is_supported_extension("") is False

    def test_is_supported_extension_case_insensitive(self):
        assert MimeRegistry.is_supported_extension(".PDF") is True
        assert MimeRegistry.is_supported_extension(".PdF") is True

    def test_get_canonical_extension_pdf(self):
        assert MimeRegistry.get_canonical_extension("application/pdf") == ".pdf"

    def test_get_canonical_extension_mp4(self):
        assert MimeRegistry.get_canonical_extension("video/mp4") == ".mp4"

    def test_get_canonical_extension_unknown_returns_none(self):
        assert MimeRegistry.get_canonical_extension("application/x-unknown") is None

    def test_get_allowed_mimes_for_extension_mp3(self):
        allowed = MimeRegistry.get_allowed_mimes_for_extension(".mp3")
        assert "audio/mpeg" in allowed

    def test_get_allowed_mimes_for_extension_wav(self):
        allowed = MimeRegistry.get_allowed_mimes_for_extension(".wav")
        assert "audio/wav" in allowed
        assert "audio/x-wav" in allowed

    def test_get_allowed_mimes_for_extension_unknown(self):
        assert MimeRegistry.get_allowed_mimes_for_extension(".xyz") == []

    def test_get_allowed_mimes_case_insensitive(self):
        assert MimeRegistry.get_allowed_mimes_for_extension(
            ".MP3"
        ) == MimeRegistry.get_allowed_mimes_for_extension(".mp3")

    def test_get_authoritative_mime_magic_wins(self):
        # When magic gives a real MIME, it takes precedence
        result = MimeRegistry.get_authoritative_mime("document.docx", "application/pdf")
        assert result == "application/pdf"

    def test_get_authoritative_mime_falls_back_to_extension(self):
        # When magic returns the default, fall back to extension-based guess
        result = MimeRegistry.get_authoritative_mime("file.pdf", "application/octet-stream")
        # Python stdlib should recognise .pdf
        assert "pdf" in result.lower()

    def test_get_authoritative_mime_truly_unknown(self):
        result = MimeRegistry.get_authoritative_mime("file.xyzabc", "application/octet-stream")
        assert result == "application/octet-stream"

    def test_allowed_extensions_not_empty(self):
        assert len(ALLOWED_EXTENSIONS) > 0

    def test_text_code_extensions_present(self):
        # Ensure a sample of code extensions passed through
        for ext in (".py", ".js", ".rs", ".go", ".ts"):
            assert ext in ALLOWED_EXTENSIONS, f"{ext} should be in ALLOWED_EXTENSIONS"

    def test_resolve_upload_mime_preserves_valid(self):
        assert MimeRegistry.resolve_upload_mime("test.pdf", "application/pdf") == "application/pdf"
        assert MimeRegistry.resolve_upload_mime("test.png", "image/png") == "image/png"

    def test_resolve_upload_mime_extension_mapping(self):
        # Mapped extensions resolve to their first mapped MIME
        assert (
            MimeRegistry.resolve_upload_mime("test.pdf", "application/octet-stream")
            == "application/pdf"
        )
        assert (
            MimeRegistry.resolve_upload_mime("test.tex", "application/octet-stream")
            == "application/x-tex"
        )

    def test_resolve_upload_mime_guess_type(self):
        # Checks python mimetypes database for allowed mimes
        assert (
            MimeRegistry.resolve_upload_mime("test.py", "application/octet-stream")
            == "text/x-python"
        )

    def test_resolve_upload_mime_fallback_to_text(self):
        # Any explicitly allowed extension with unknown mimetype defaults to text/plain
        # .dockerfile is allowed but not in EXTENSION_MAPPING or standard mimetypes guess
        assert (
            MimeRegistry.resolve_upload_mime("Dockerfile", "application/octet-stream")
            == "text/plain"
        )

    def test_resolve_upload_mime_unsupported(self):
        # Completely unknown extensions fall back to raw_mime
        assert (
            MimeRegistry.resolve_upload_mime("test.xyzabc", "application/octet-stream")
            == "application/octet-stream"
        )
