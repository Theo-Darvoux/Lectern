"""Tests for Phase 1B security hardening in file_security.py.

Covers:
- PIL decompression bomb protection (MAX_IMAGE_PIXELS)
- PDF dangerous action detection (expanded action keys + page tree walk)
"""

from pathlib import Path

import pikepdf
import pytest
from PIL import Image

from app.core.security.file_security import check_pdf_safety
from app.core.security.file_security._pdf import (
    _PDF_DANGEROUS_ACTION_KEYS,
    _apply_pdf_security_strip,
    _walk_page_tree_for_actions,
)

# ── PIL MAX_IMAGE_PIXELS ────────────────────────────────────────────────


class TestPilMaxPixels:
    def test_core_sets_pillow_global_limit(self):
        assert Image.MAX_IMAGE_PIXELS == 25_000_000


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

    def test_annotation_additional_action_detected(self, tmp_path):
        pdf_path = _make_pdf(tmp_path)
        with pikepdf.open(str(pdf_path), allow_overwriting_input=True) as pdf:
            annotation = pikepdf.Dictionary(
                Type=pikepdf.Name("/Annot"),
                Subtype=pikepdf.Name("/Text"),
                Rect=pikepdf.Array([0, 0, 10, 10]),
                AA=pikepdf.Dictionary(
                    E=pikepdf.Dictionary(
                        S=pikepdf.Name("/JavaScript"), JS=pikepdf.String("app.alert(1)")
                    )
                ),
            )
            pdf.pages[0]["/Annots"] = pikepdf.Array([pdf.make_indirect(annotation)])
            pdf.save(str(pdf_path))

        with pytest.raises(ValueError, match="dangerous action"):
            check_pdf_safety(pdf_path)

    @pytest.mark.parametrize(
        "target",
        [
            "https://example.com/notes",
            "http://example.com/notes",
            "mailto:teacher@example.com",
        ],
    )
    def test_safe_external_annotation_links_are_allowed_and_preserved(self, tmp_path, target):
        pdf_path = _make_pdf(tmp_path)
        with pikepdf.open(str(pdf_path), allow_overwriting_input=True) as pdf:
            annotation = pikepdf.Dictionary(
                Type=pikepdf.Name("/Annot"),
                Subtype=pikepdf.Name("/Link"),
                Rect=pikepdf.Array([0, 0, 10, 10]),
                A=pikepdf.Dictionary(
                    S=pikepdf.Name("/URI"),
                    URI=pikepdf.String(target),
                ),
            )
            pdf.pages[0]["/Annots"] = pikepdf.Array([pdf.make_indirect(annotation)])
            pdf.save(str(pdf_path))

        check_pdf_safety(pdf_path)
        with pikepdf.open(str(pdf_path)) as pdf:
            _apply_pdf_security_strip(pdf)
            action = pdf.pages[0]["/Annots"][0]["/A"]
            assert str(action["/S"]) == "/URI"
            assert str(action["/URI"]) == target

    def test_external_annotation_links_can_be_disabled(self, tmp_path, monkeypatch):
        from app.config import settings

        pdf_path = _make_pdf(tmp_path)
        with pikepdf.open(str(pdf_path), allow_overwriting_input=True) as pdf:
            annotation = pikepdf.Dictionary(
                Type=pikepdf.Name("/Annot"),
                Subtype=pikepdf.Name("/Link"),
                Rect=pikepdf.Array([0, 0, 10, 10]),
                A=pikepdf.Dictionary(
                    S=pikepdf.Name("/URI"),
                    URI=pikepdf.String("https://example.com/notes"),
                ),
            )
            pdf.pages[0]["/Annots"] = pikepdf.Array([pdf.make_indirect(annotation)])
            pdf.save(str(pdf_path))

        monkeypatch.setattr(settings, "allow_external_document_links", False)
        with pytest.raises(ValueError, match="dangerous action"):
            check_pdf_safety(pdf_path)

    @pytest.mark.parametrize("target", ["javascript:alert(1)", "file:///etc/passwd", "https:"])
    def test_unsafe_external_annotation_links_are_rejected(self, tmp_path, target):
        pdf_path = _make_pdf(tmp_path)
        with pikepdf.open(str(pdf_path), allow_overwriting_input=True) as pdf:
            annotation = pikepdf.Dictionary(
                Type=pikepdf.Name("/Annot"),
                Subtype=pikepdf.Name("/Link"),
                Rect=pikepdf.Array([0, 0, 10, 10]),
                A=pikepdf.Dictionary(
                    S=pikepdf.Name("/URI"),
                    URI=pikepdf.String(target),
                ),
            )
            pdf.pages[0]["/Annots"] = pikepdf.Array([pdf.make_indirect(annotation)])
            pdf.save(str(pdf_path))

        with pytest.raises(ValueError, match="prohibited external hyperlink"):
            check_pdf_safety(pdf_path)

    @pytest.mark.parametrize("subtype", ["/RichMedia", "/Screen", "/Movie"])
    def test_active_annotation_subtype_detected(self, tmp_path, subtype):
        pdf_path = _make_pdf(tmp_path)
        with pikepdf.open(str(pdf_path), allow_overwriting_input=True) as pdf:
            annotation = pikepdf.Dictionary(
                Type=pikepdf.Name("/Annot"),
                Subtype=pikepdf.Name(subtype),
                Rect=pikepdf.Array([0, 0, 10, 10]),
            )
            pdf.pages[0]["/Annots"] = pikepdf.Array([pdf.make_indirect(annotation)])
            pdf.save(str(pdf_path))

        with pytest.raises(ValueError, match="active content"):
            check_pdf_safety(pdf_path)

    @pytest.mark.parametrize("action_subtype", ["/Rendition", "/CustomAction"])
    def test_unknown_annotation_actions_fail_closed(self, tmp_path, action_subtype):
        pdf_path = _make_pdf(tmp_path)
        with pikepdf.open(str(pdf_path), allow_overwriting_input=True) as pdf:
            annotation = pikepdf.Dictionary(
                Type=pikepdf.Name("/Annot"),
                Subtype=pikepdf.Name("/Link"),
                Rect=pikepdf.Array([0, 0, 10, 10]),
                A=pikepdf.Dictionary(S=pikepdf.Name(action_subtype)),
            )
            pdf.pages[0]["/Annots"] = pikepdf.Array([pdf.make_indirect(annotation)])
            pdf.save(str(pdf_path))

        with pytest.raises(ValueError, match="dangerous action"):
            check_pdf_safety(pdf_path)

    def test_xfa_form_detected(self, tmp_path):
        pdf_path = _make_pdf(tmp_path)
        with pikepdf.open(str(pdf_path), allow_overwriting_input=True) as pdf:
            pdf.Root["/AcroForm"] = pikepdf.Dictionary(
                Fields=pikepdf.Array(), XFA=pikepdf.String("<script/>")
            )
            pdf.save(str(pdf_path))

        with pytest.raises(ValueError, match="XFA"):
            check_pdf_safety(pdf_path)

    def test_corrupt_pdf_fails_closed(self, tmp_path):
        p = tmp_path / "corrupt.pdf"
        p.write_bytes(b"not-a-pdf")
        with pytest.raises(ValueError, match="malformed"):
            check_pdf_safety(p)


class TestPdfDangerousActionKeys:
    def test_flat_keys_present(self):
        expected = {"/AA", "/Launch", "/SubmitForm", "/ImportData"}
        assert expected == _PDF_DANGEROUS_ACTION_KEYS


class TestWalkPageTreeForActions:
    def test_depth_guard(self):
        node = pikepdf.Dictionary()
        with pytest.raises(ValueError, match="depth"):
            _walk_page_tree_for_actions(node, depth=51)

    def test_detects_nested_action(self):
        child = pikepdf.Dictionary({"/Launch": pikepdf.String("cmd")})
        parent = pikepdf.Dictionary({"/Kids": pikepdf.Array([child])})
        with pytest.raises(ValueError, match="/Launch"):
            _walk_page_tree_for_actions(parent)


class TestOfficeSanitization:
    def test_docx_content_types_preserves_unprefixed_namespace(self, tmp_path):
        import zipfile

        from app.core.security.file_security._office import _zip_strip_file

        docx_path = tmp_path / "test.docx"
        with zipfile.ZipFile(docx_path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(
                "[Content_Types].xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                "</Types>",
            )
            z.writestr(
                "_rels/.rels",
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                "</Relationships>",
            )
            z.writestr(
                "word/document.xml",
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>',
            )

        out_path = tmp_path / "sanitized.docx"
        _zip_strip_file(
            docx_path,
            out_path,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

        with zipfile.ZipFile(out_path, "r") as z:
            ct = z.read("[Content_Types].xml").decode("utf-8")
            assert "<ns0:Types" not in ct
            assert "<Types" in ct
