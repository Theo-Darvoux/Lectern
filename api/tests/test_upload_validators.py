"""Tests for upload/validators.py — filename validation, MIME correction, size checks."""

import pytest

from app.core.exceptions import BadRequestError
from app.core.upload_errors import (
    ERR_FILE_TOO_LARGE,
    ERR_FILENAME_TOO_LONG,
    ERR_MIME_MISMATCH,
    ERR_TYPE_NOT_ALLOWED,
)
from app.routers.upload.validators import (
    _MAX_FILENAME_LENGTH,
    _apply_mime_correction,
    _check_per_type_size,
    _sanitize_filename,
    _validate_filename,
)

# ── _sanitize_filename ──────────────────────────────────────────────────


class TestSanitizeFilename:
    def test_strips_control_characters(self):
        assert _sanitize_filename("file\x00name\x1f.pdf") == "filename.pdf"

    def test_strips_unicode_trickery(self):
        # Zero-width space, BIDI override
        assert _sanitize_filename("file\u200bname\u202a.pdf") == "filename.pdf"

    def test_replaces_shell_special_chars(self):
        # & stripped, $ kept, space→_
        assert _sanitize_filename("file name$test&foo.pdf") == "file_name$test_foo.pdf"

    def test_collapses_underscores(self):
        assert _sanitize_filename("file___name.pdf") == "file_name.pdf"

    def test_strips_leading_trailing_dots(self):
        assert _sanitize_filename("..hidden.pdf") == "hidden.pdf"

    def test_extracts_basename(self):
        assert _sanitize_filename("/etc/passwd") == "passwd"
        assert _sanitize_filename("../../secrets.txt") == "secrets.txt"

    def test_path_traversal_attack(self):
        result = _sanitize_filename("../../../etc/passwd")
        assert "/" not in result
        assert ".." not in result

    def test_empty_after_sanitization(self):
        # $ is now kept, use chars that are still stripped
        assert _sanitize_filename("!!!") == ""

    def test_preserves_kept_special_chars(self):
        assert (
            _sanitize_filename("Cours #1 - L'essentiel (€5).pdf")
            == "Cours__1_-_L'essentiel_(€5).pdf"
        )
        assert _sanitize_filename('notes "avancées" [2024].pdf') == 'notes_"avancées"_[2024].pdf'
        assert _sanitize_filename("file$var=val@host{key}.pdf") == "file$var=val@host{key}.pdf"

    def test_preserves_accents_and_capitalization(self):
        assert _sanitize_filename("Théorème_de_Pythagore.pdf") == "Théorème_de_Pythagore.pdf"
        assert _sanitize_filename("UPPERCASE_File.pdf") == "UPPERCASE_File.pdf"


# ── _validate_filename ───────────────────────────────────────────────────


class TestValidateFilename:
    def test_valid_pdf(self):
        name, ext = _validate_filename("report.pdf")
        assert name == "report.pdf"
        assert ext == ".pdf"

    def test_valid_png(self):
        name, ext = _validate_filename("photo.png")
        assert ext == ".png"

    def test_empty_filename_raises(self):
        with pytest.raises(BadRequestError) as exc_info:
            _validate_filename("")
        assert exc_info.value.code == ERR_TYPE_NOT_ALLOWED

    def test_all_special_chars_raises(self):
        with pytest.raises(BadRequestError) as exc_info:
            _validate_filename("$$$")
        assert exc_info.value.code == ERR_TYPE_NOT_ALLOWED

    def test_unsupported_extension_raises(self):
        with pytest.raises(BadRequestError) as exc_info:
            _validate_filename("malware.exe")
        assert exc_info.value.code == ERR_TYPE_NOT_ALLOWED

    def test_filename_too_long_raises(self):
        long_name = "a" * (_MAX_FILENAME_LENGTH + 10) + ".pdf"
        with pytest.raises(BadRequestError) as exc_info:
            _validate_filename(long_name)
        assert exc_info.value.code == ERR_FILENAME_TOO_LONG

    def test_filename_at_max_length_ok(self):
        # Exactly at the limit should pass (assuming extension is supported)
        name = "a" * (_MAX_FILENAME_LENGTH - 4) + ".pdf"
        result_name, ext = _validate_filename(name)
        assert len(result_name) == _MAX_FILENAME_LENGTH
        assert ext == ".pdf"


# ── _check_per_type_size ─────────────────────────────────────────────────


class TestCheckPerTypeSize:
    def test_small_image_passes(self):
        # 1 MiB image should pass
        _check_per_type_size("image/png", 1 * 1024 * 1024)

    def test_image_exceeds_limit(self):
        with pytest.raises(BadRequestError) as exc_info:
            _check_per_type_size("image/png", 999 * 1024 * 1024)
        assert exc_info.value.code == ERR_FILE_TOO_LARGE

    def test_svg_has_lower_limit(self):
        # SVGs have a tighter limit than general images
        with pytest.raises(BadRequestError) as exc_info:
            # Use a size that exceeds SVG limit but not general image limit
            _check_per_type_size("image/svg+xml", 100 * 1024 * 1024)
        assert exc_info.value.code == ERR_FILE_TOO_LARGE

    def test_global_limit_catches_unknown_type(self):
        with pytest.raises(BadRequestError) as exc_info:
            _check_per_type_size("application/octet-stream", 999 * 1024 * 1024)
        assert exc_info.value.code == ERR_FILE_TOO_LARGE

    def test_prefix_matching(self):
        # "audio/" prefix should match "audio/mpeg"
        _check_per_type_size("audio/mpeg", 1 * 1024 * 1024)


# ── _apply_mime_correction ───────────────────────────────────────────────


class TestApplyMimeCorrection:
    def test_disallowed_mime_raises(self):
        with pytest.raises(BadRequestError) as exc_info:
            _apply_mime_correction("file.pdf", "application/x-executable", ".pdf")
        assert exc_info.value.code == ERR_TYPE_NOT_ALLOWED

    def test_extension_mime_mismatch_raises(self):
        with pytest.raises(BadRequestError) as exc_info:
            _apply_mime_correction("file.png", "image/jpeg", ".png")
        assert exc_info.value.code == ERR_MIME_MISMATCH

    def test_matching_extension_and_mime_passes(self):
        name, ext = _apply_mime_correction("photo.jpg", "image/jpeg", ".jpg")
        assert name == "photo.jpg"
        assert ext == ".jpg"
