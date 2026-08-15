"""Tests for app.core.security.polyglot — polyglot file detection."""

import pytest

from app.core.security.polyglot import check_polyglot


def _write(tmp_path, name: str, data: bytes):
    p = tmp_path / name
    p.write_bytes(data)
    return p


# ── Clean files pass ─────────────────────────────────────────────────────────


def test_clean_jpeg_passes(tmp_path):
    """A standard JPEG header with no polyglot patterns passes."""
    jpeg_data = b"\xff\xd8\xff\xe0" + b"\x00" * 100
    p = _write(tmp_path, "photo.jpg", jpeg_data)
    check_polyglot(p, "image/jpeg")  # must not raise


def test_clean_pdf_passes(tmp_path):
    """A standard PDF header passes."""
    p = _write(tmp_path, "doc.pdf", b"%PDF-1.7\n%%EOF\n" + b"\x00" * 20)
    check_polyglot(p, "application/pdf")  # must not raise


def test_clean_zip_passes(tmp_path):
    """A valid ZIP file is not a polyglot when declared as ZIP."""
    zip_data = b"PK\x03\x04" + b"\x00" * 50 + b"PK\x05\x06" + b"\x00" * 22
    p = _write(tmp_path, "archive.zip", zip_data)
    check_polyglot(p, "application/zip")  # must not raise


def test_clean_html_passes(tmp_path):
    """An HTML file with HTML magic bytes passes when declared as text/html."""
    p = _write(tmp_path, "page.html", b"<!DOCTYPE html><html><body></body></html>")
    check_polyglot(p, "text/html")  # must not raise


def test_opendocument_passes(tmp_path):
    """An OpenDocument file (e.g. ODS, ODT) with ZIP magic bytes passes."""
    data = (
        b"PK\x03\x04"
        + b"\x00" * 50
        + b"mimetypeapplication/vnd.oasis.opendocument.spreadsheet"
        + b"\x00" * 22
    )
    p = _write(tmp_path, "sheet.ods", data)
    check_polyglot(p, "application/vnd.oasis.opendocument.spreadsheet")  # must not raise


def test_epub_passes(tmp_path):
    """An EPUB file with ZIP magic bytes passes."""
    data = b"PK\x03\x04" + b"\x00" * 50 + b"mimetypeapplication/epub+zip" + b"\x00" * 22
    p = _write(tmp_path, "book.epub", data)
    check_polyglot(p, "application/epub+zip")  # must not raise


def test_small_file_passes(tmp_path):
    """Files under 4 bytes are skipped (too small to be meaningful)."""
    p = _write(tmp_path, "tiny.bin", b"AB")
    check_polyglot(p, "image/png")  # must not raise


# ── Polyglot header detections ───────────────────────────────────────────────


def test_jpeg_with_zip_header_fails(tmp_path):
    """A file starting with ZIP magic but declared as JPEG is rejected."""
    data = b"PK\x03\x04" + b"\x00" * 100
    p = _write(tmp_path, "evil.jpg", data)
    with pytest.raises(ValueError, match="archive"):
        check_polyglot(p, "image/jpeg")


def test_pdf_with_executable_header_fails(tmp_path):
    """A file starting with PE magic but declared as PDF is rejected."""
    data = b"MZ\x90\x00" + b"\x00" * 100
    p = _write(tmp_path, "evil.pdf", data)
    with pytest.raises(ValueError, match="executable"):
        check_polyglot(p, "application/pdf")


def test_image_with_elf_magic_fails(tmp_path):
    """A file starting with ELF magic but declared as image is rejected."""
    data = b"\x7fELF\x02\x01" + b"\x00" * 100
    p = _write(tmp_path, "evil.png", data)
    with pytest.raises(ValueError, match="executable"):
        check_polyglot(p, "image/png")


def test_image_with_html_script_tag_fails(tmp_path):
    """A file starting with <script> but declared as image is rejected."""
    data = b"<script>alert(1)</script>" + b"\x00" * 100
    p = _write(tmp_path, "xss.jpg", data)
    with pytest.raises(ValueError, match="html"):
        check_polyglot(p, "image/jpeg")


def test_image_with_java_class_magic_fails(tmp_path):
    """A file starting with Java class magic declared as image is rejected."""
    data = b"\xca\xfe\xba\xbe" + b"\x00" * 100
    p = _write(tmp_path, "evil.jpg", data)
    with pytest.raises(ValueError, match="executable"):
        check_polyglot(p, "image/jpeg")


# ── ZIP tail detection ───────────────────────────────────────────────────────


def test_jpeg_with_appended_zip_fails(tmp_path):
    """A JPEG with a ZIP EOCD record appended at the tail is rejected (JZBOMB)."""
    jpeg_magic = b"\xff\xd8\xff\xe0" + b"\x00" * 200
    zip_eocd = b"PK\x05\x06" + b"\x00" * 18  # minimal ZIP EOCD record
    p = _write(tmp_path, "bomb.jpg", jpeg_magic + zip_eocd)
    with pytest.raises(ValueError, match="ZIP End-of-Central-Directory"):
        check_polyglot(p, "image/jpeg")


def test_pdf_with_appended_zip_fails(tmp_path):
    """A PDF with a ZIP EOCD appended is rejected."""
    pdf_data = b"%PDF-1.7\n" + b"\x00" * 200
    zip_eocd = b"PK\x05\x06" + b"\x00" * 18
    p = _write(tmp_path, "bomb.pdf", pdf_data + zip_eocd)
    with pytest.raises(ValueError, match="ZIP End-of-Central-Directory"):
        check_polyglot(p, "application/pdf")


def test_zip_tail_allowed_in_zip_mime(tmp_path):
    """A ZIP file's EOCD record at the tail is not flagged when MIME is zip."""
    data = b"PK\x03\x04" + b"\x00" * 50 + b"PK\x05\x06" + b"\x00" * 22
    p = _write(tmp_path, "archive.zip", data)
    check_polyglot(p, "application/zip")  # must not raise


# ── Edge cases ───────────────────────────────────────────────────────────────


def test_unreadable_file_raises_fail_closed(tmp_path):
    """If the file cannot be read, check_polyglot must fail closed (raise ValueError)."""
    nonexistent = tmp_path / "ghost.jpg"
    with pytest.raises(ValueError, match="structural check unreachable"):
        check_polyglot(nonexistent, "image/jpeg")
