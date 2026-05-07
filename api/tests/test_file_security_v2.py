"""Tests for Phase 1B security hardening in file_security.py.

Covers:
- PIL decompression bomb protection (MAX_IMAGE_PIXELS)
- PDF dangerous action detection (expanded action keys + page tree walk)
"""

from pathlib import Path

import pikepdf
import pytest
from PIL import Image

from app.core.file_security import (
    _PDF_DANGEROUS_ACTION_KEYS,
    _walk_page_tree_for_actions,
    check_pdf_safety,
)

# ── PIL MAX_IMAGE_PIXELS ────────────────────────────────────────────────


class TestPilMaxPixels:
    def test_max_image_pixels_is_set(self):
        assert Image.MAX_IMAGE_PIXELS == 50_000_000


# ── PDF action checks ──────────────────────────────────────────────────


def _make_pdf(tmp_path: Path, **catalog_extras) -> Path:
    """Create a minimal PDF with optional catalog entries."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page()
    for key, value in catalog_extras.items():
        pdf.Root[pikepdf.Name(key)] = value
    out = tmp_path / "test.pdf"
    pdf.save(str(out))
    return out


class TestCheckPdfSafety:
    def test_clean_pdf_passes(self, tmp_path):
        pdf_path = _make_pdf(tmp_path)
        check_pdf_safety(pdf_path)  # Should not raise

    @pytest.mark.parametrize(
        "action_key",
        [
            "/OpenAction",
            "/AA",
            "/Launch",
            "/SubmitForm",
            "/ImportData",
        ],
    )
    def test_catalog_action_detected(self, tmp_path, action_key):
        pdf_path = _make_pdf(tmp_path, **{action_key: pikepdf.String("malicious")})
        with pytest.raises(ValueError, match="auto-executing action"):
            check_pdf_safety(pdf_path)

    def test_javascript_in_names_detected(self, tmp_path):
        pdf_path = _make_pdf(tmp_path)
        with pikepdf.open(str(pdf_path), allow_overwriting_input=True) as pdf:
            names = pikepdf.Dictionary()
            names[pikepdf.Name("/JavaScript")] = pikepdf.Array()
            pdf.Root[pikepdf.Name("/Names")] = names
            pdf.save(str(pdf_path))
        with pytest.raises(ValueError, match="JavaScript"):
            check_pdf_safety(pdf_path)

    def test_page_level_action_detected(self, tmp_path):
        pdf_path = _make_pdf(tmp_path)
        with pikepdf.open(str(pdf_path), allow_overwriting_input=True) as pdf:
            page = pdf.pages[0]
            page[pikepdf.Name("/AA")] = pikepdf.String("trigger")
            pdf.save(str(pdf_path))
        with pytest.raises(ValueError, match="dangerous action"):
            check_pdf_safety(pdf_path)

    def test_corrupt_pdf_fails_closed(self, tmp_path):
        p = tmp_path / "corrupt.pdf"
        p.write_bytes(b"not-a-pdf")
        with pytest.raises(ValueError, match="malformed"):
            check_pdf_safety(p)


class TestPdfDangerousActionKeys:
    def test_all_five_keys_present(self):
        expected = {"/OpenAction", "/AA", "/Launch", "/SubmitForm", "/ImportData"}
        assert expected == _PDF_DANGEROUS_ACTION_KEYS


class TestWalkPageTreeForActions:
    def test_depth_guard(self):
        # Should return silently at depth > 50 (no infinite recursion)
        node = pikepdf.Dictionary()
        _walk_page_tree_for_actions(node, depth=51)  # Should not raise

    def test_detects_nested_action(self):
        child = pikepdf.Dictionary({"/Launch": pikepdf.String("cmd")})
        parent = pikepdf.Dictionary({"/Kids": pikepdf.Array([child])})
        with pytest.raises(ValueError, match="/Launch"):
            _walk_page_tree_for_actions(parent)
