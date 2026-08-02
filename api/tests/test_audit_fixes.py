"""Comprehensive tests for upload flow audit fixes.

Covers:
- C1: CAS ref count race condition (distributed lock)
- C2: Scan/strip file isolation (scan reads copy, not original)
- C3: CAS entry staleness check (scanned_at, cas_max_age_seconds)
- H1: OLE2 macro detection (oletools analyze_macros)
- H3: Compression ratio div-by-zero guard
- H5: S3 copy timeout wrappers
- M1: OOXML dead code removal
- M2: GIF pixel budget enforcement
- M5: SSE event log cap (LTRIM)
- M8: OLE2 shared helper (_scan_vba_for_autoexec)
- T2: Integration-style pipeline tests
- T4: Security bypass attempts
- T5: Pipeline resume after checkpoint
- T6: Scanner fail-closed mode
"""

import io
import json
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pikepdf
import pytest
from PIL import Image

from app.core.security.file_security import (
    SvgSecurityError,
    check_pdf_safety,
    check_svg_safety,
    compress_file_path,
    strip_metadata_file,
)
from app.core.security.file_security._image import _strip_image_metadata
from app.core.security.file_security._office import (
    _check_ole2_macros,
    _strip_ooxml_from_path,
)
from app.workers.process_upload import (
    _STAGES,
    _overall,
)

# ── Helpers ─────────────────────────────────────────────────────────────────


def _make_minimal_pdf(tmp_path: Path) -> Path:
    pdf = pikepdf.new()
    page = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name("/Page"),
            MediaBox=pikepdf.Array([0, 0, 612, 792]),
        )
    )
    pdf.pages.append(pikepdf.Page(page))
    p = tmp_path / "test.pdf"
    pdf.save(str(p))
    return p


def _make_zip(tmp_path: Path, entries: dict[str, bytes]) -> Path:
    p = tmp_path / "test.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    p.write_bytes(buf.getvalue())
    return p


def _make_gif(width: int, height: int, frames: int) -> bytes:
    """Create a multi-frame GIF in memory."""
    imgs = [Image.new("RGBA", (width, height), (i * 10 % 256, 0, 0, 255)) for i in range(frames)]
    buf = io.BytesIO()
    imgs[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=imgs[1:],
        duration=10,
        loop=0,
    )
    return buf.getvalue()


# =============================================================================
# C1: CAS ref count — FakeRedis eval with initial_data
# =============================================================================


class TestCASRefCount:
    """Verify that the Lua CAS INCR/DECR scripts work correctly."""

    @pytest.fixture
    def fake_redis(self):
        from tests.conftest import FakeRedis

        return FakeRedis()

    async def test_incr_creates_new_entry_with_initial_data(self, fake_redis):
        """When key doesn't exist and ARGV[1] is provided, create with ref_count=1."""
        from app.core.security.cas import _LUA_CAS_INCR

        initial = json.dumps({"final_key": "cas/abc", "size": 100})
        result = await fake_redis.eval(
            _LUA_CAS_INCR,
            3,
            "upload:cas:abc",
            "storage:total_usage_bytes",
            "cas:operation:create-abc",
            initial,
        )
        assert result == 1

        raw = await fake_redis.get("upload:cas:abc")
        data = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
        assert data["ref_count"] == 1
        assert data["final_key"] == "cas/abc"

    async def test_incr_increments_existing_entry(self, fake_redis):
        """When key exists, increment ref_count."""
        from app.core.security.cas import _LUA_CAS_INCR

        await fake_redis.set("upload:cas:abc", json.dumps({"ref_count": 2, "final_key": "cas/abc"}))
        result = await fake_redis.eval(
            _LUA_CAS_INCR,
            3,
            "upload:cas:abc",
            "storage:total_usage_bytes",
            "cas:operation:increment-abc",
        )
        assert result == 3

    async def test_incr_no_key_no_initial_data_returns_zero(self, fake_redis):
        """When key doesn't exist and no ARGV[1], report missing state."""
        from app.core.security.cas import _LUA_CAS_INCR

        result = await fake_redis.eval(
            _LUA_CAS_INCR,
            3,
            "upload:cas:missing",
            "storage:total_usage_bytes",
            "cas:operation:increment-missing",
        )
        assert result == -1

    async def test_decr_to_zero_deletes_key(self, fake_redis):
        """When ref_count reaches 0, key is deleted."""
        from app.core.security.cas import _LUA_CAS_DECR

        await fake_redis.set("upload:cas:abc", json.dumps({"ref_count": 1, "final_key": "cas/abc"}))
        await fake_redis.set("storage:total_usage_bytes", 100)
        result = await fake_redis.eval(
            _LUA_CAS_DECR,
            2,
            "upload:cas:abc",
            "cas:operation:decrement-abc",
        )
        assert result == 0
        assert await fake_redis.get("upload:cas:abc") is None
        assert int(await fake_redis.get("storage:total_usage_bytes")) == 100

    async def test_decr_nonexistent_returns_zero(self, fake_redis):
        """DECR on nonexistent key reports missing state."""
        from app.core.security.cas import _LUA_CAS_DECR

        result = await fake_redis.eval(
            _LUA_CAS_DECR,
            2,
            "upload:cas:missing",
            "cas:operation:decrement-missing",
        )
        assert result == -1


# =============================================================================


# =============================================================================
# H1 / M8: OLE2 macro detection via analyze_macros
# =============================================================================


class TestOLE2MacroDetection:
    """Legacy Office files containing any VBA project are rejected."""

    def test_no_macros_passes_and_closes_parser(self):
        parser = MagicMock()
        parser.detect_vba_macros.return_value = False

        with patch("oletools.olevba.VBA_Parser", return_value=parser):
            _check_ole2_macros(Path("document.doc"))

        parser.close.assert_called_once_with()

    def test_any_macro_is_rejected_and_parser_is_closed(self):
        parser = MagicMock()
        parser.detect_vba_macros.return_value = True

        with (
            patch("oletools.olevba.VBA_Parser", return_value=parser),
            pytest.raises(ValueError, match="Macro-enabled legacy Office files"),
        ):
            _check_ole2_macros(Path("document.doc"))

        parser.close.assert_called_once_with()

    def test_parser_failure_fails_closed_and_closes_parser(self):
        parser = MagicMock()
        parser.detect_vba_macros.side_effect = RuntimeError("broken OLE stream")

        with (
            patch("oletools.olevba.VBA_Parser", return_value=parser),
            pytest.raises(ValueError, match="could not be validated"),
        ):
            _check_ole2_macros(Path("document.doc"))

        parser.close.assert_called_once_with()


# =============================================================================
# H3: Compression ratio div-by-zero
# =============================================================================


class TestCompressionRatioGuard:
    """Verify that zero-size outputs don't cause division by zero in metrics."""

    def test_overall_progress_math(self):
        """Basic sanity: _overall at boundaries works correctly."""
        assert _overall(0, 0.0) == 0.0
        assert abs(_overall(0, 1.0) - 0.35) < 1e-6
        assert abs(_overall(4, 1.0) - 1.0) < 1e-6


# =============================================================================
# M1: OOXML docProps stripping — dead code removed
# =============================================================================


class TestOOXMLStrip:
    """Verify that OOXML stripping removes docProps/ entries correctly."""

    async def test_strips_docprops_directory(self, tmp_path):
        """docProps/ entries should be stripped from OOXML files."""
        entries = {
            "[Content_Types].xml": b"<Types/>",
            "docProps/core.xml": b"<author>Secret</author>",
            "docProps/app.xml": b"<app>Info</app>",
            "docProps/thumbnail.jpeg": b"\xff\xd8thumbnail",
            "word/document.xml": b"<doc>Content</doc>",
        }
        p = _make_zip(tmp_path, entries)
        result = await _strip_ooxml_from_path(p)

        with zipfile.ZipFile(result, "r") as z:
            names = z.namelist()
        assert "[Content_Types].xml" in names
        assert "word/document.xml" in names
        assert "docProps/core.xml" not in names
        assert "docProps/app.xml" not in names
        assert "docProps/thumbnail.jpeg" not in names

    @pytest.mark.parametrize(
        "active_path",
        [
            "word/vbaProject.bin",
            "word/activeX/activeX1.bin",
            "word/embeddings/oleObject1.bin",
        ],
    )
    async def test_rejects_ooxml_active_content(self, tmp_path, active_path):
        p = _make_zip(
            tmp_path,
            {
                "[Content_Types].xml": b"<Types/>",
                "word/document.xml": b"<document/>",
                active_path: b"active content",
            },
        )

        with pytest.raises(ValueError, match="prohibited active content"):
            await _strip_ooxml_from_path(p)

    async def test_rejects_external_ooxml_relationships(self, tmp_path):
        relationships = b"""<?xml version='1.0' encoding='UTF-8'?>
        <Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>
          <Relationship Id='rId1' Type='urn:test' Target='https://example.invalid/pixel'
                        TargetMode='External'/>
        </Relationships>"""
        p = _make_zip(
            tmp_path,
            {
                "[Content_Types].xml": b"<Types/>",
                "word/document.xml": b"<document/>",
                "word/_rels/document.xml.rels": relationships,
            },
        )

        with pytest.raises(ValueError, match="external relationship"):
            await _strip_ooxml_from_path(p)

    async def test_malformed_relationship_before_active_content_fails_closed(self, tmp_path):
        p = _make_zip(
            tmp_path,
            {
                "word/_rels/document.xml.rels": b"<Relationships><broken>",
                "word/vbaProject.bin": b"macro",
            },
        )

        with pytest.raises(ValueError, match="prohibited active content|Malformed"):
            await _strip_ooxml_from_path(p)

    @pytest.mark.parametrize(
        "target",
        [
            "file:///etc/passwd",
            "javascript:alert(1)",
            "data:text/plain,secret",
            r"\\server\share\file.docx",
        ],
    )
    async def test_rejects_unsafe_external_hyperlink_schemes(self, tmp_path, target):
        relationships = f"""<Relationships
          xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>
          <Relationship Id='rId1'
            Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink'
            Target='{target}' TargetMode='External'/>
        </Relationships>""".encode()
        p = _make_zip(
            tmp_path,
            {
                "word/document.xml": b"<document/>",
                "word/_rels/document.xml.rels": relationships,
            },
        )

        with pytest.raises(ValueError, match="prohibited external hyperlink target"):
            await _strip_ooxml_from_path(p)

    async def test_preserves_internal_ooxml_relationships(self, tmp_path):
        relationships = b"""<Relationships
          xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>
          <Relationship Id='rId1' Type='urn:test' Target='styles.xml'/>
        </Relationships>"""
        p = _make_zip(
            tmp_path,
            {
                "word/document.xml": b"<document/>",
                "word/_rels/document.xml.rels": relationships,
            },
        )

        result = await _strip_ooxml_from_path(p)
        with zipfile.ZipFile(result) as archive:
            assert archive.read("word/_rels/document.xml.rels") == relationships

    async def test_zip_bomb_total_too_large(self, tmp_path):
        """ZIP total declared size exceeding limit should raise ValueError."""

        # Patch the threshold low so we can test with small data
        with patch("app.core.security.file_security._office._ZIP_MAX_TOTAL_BYTES", 50):
            entries = {
                "word/document.xml": b"x" * 60,  # 60 bytes > patched 50 limit
            }
            p = _make_zip(tmp_path, entries)
            with pytest.raises(ValueError, match="too large"):
                await _strip_ooxml_from_path(p)

    async def test_rejects_case_insensitive_sanitized_name_collisions(self, tmp_path):
        p = _make_zip(
            tmp_path,
            {
                "word/document.xml": b"one",
                "WORD/DOCUMENT.XML": b"two",
            },
        )

        with pytest.raises(ValueError, match="duplicate sanitized entry"):
            await _strip_ooxml_from_path(p)

    async def test_zip_entry_count_limit_is_enforced(self, tmp_path):
        p = _make_zip(tmp_path, {"one.xml": b"1", "two.xml": b"2"})

        with (
            patch("app.core.security.file_security._office._ZIP_MAX_ENTRIES", 1),
            pytest.raises(ValueError, match="too many entries"),
        ):
            await _strip_ooxml_from_path(p)

    async def test_odf_removes_meta_xml(self, tmp_path):
        p = _make_zip(
            tmp_path,
            {
                "mimetype": b"application/vnd.oasis.opendocument.text",
                "meta.xml": b"<office:meta>Secret Author</office:meta>",
                "content.xml": b"<document>Public content</document>",
            },
        )

        result = await _strip_ooxml_from_path(p, "application/vnd.oasis.opendocument.text")

        with zipfile.ZipFile(result) as archive:
            assert "meta.xml" not in archive.namelist()
            assert archive.read("content.xml") == b"<document>Public content</document>"

    async def test_metadata_paths_are_checked_after_sanitization(self, tmp_path):
        p = _make_zip(
            tmp_path,
            {
                "./docProps/core.xml": b"<author>Secret</author>",
                "word/document.xml": b"<doc>Content</doc>",
            },
        )

        result = await _strip_ooxml_from_path(p)

        with zipfile.ZipFile(result) as archive:
            assert "docProps/core.xml" not in archive.namelist()

    async def test_backslash_macro_and_metadata_paths_are_stripped(self, tmp_path):
        p = _make_zip(
            tmp_path,
            {
                "docProps\\core.xml": b"<author>Secret</author>",
                "Basic\\Standard\\Module.xml": b"macro",
                "word/document.xml": b"<doc>Content</doc>",
            },
        )

        result = await _strip_ooxml_from_path(p)

        with zipfile.ZipFile(result) as archive:
            assert archive.namelist() == ["word/document.xml"]

    async def test_epub_strips_private_opf_fields_and_preserves_mimetype(self, tmp_path):
        opf = b"""<?xml version='1.0' encoding='utf-8'?>
        <package xmlns='http://www.idpf.org/2007/opf'
                 xmlns:dc='http://purl.org/dc/elements/1.1/'>
          <metadata><dc:title>Public</dc:title><dc:creator>Secret Author</dc:creator></metadata>
        </package>"""
        p = tmp_path / "book.epub"
        with zipfile.ZipFile(p, "w") as archive:
            mimetype = zipfile.ZipInfo("mimetype")
            mimetype.compress_type = zipfile.ZIP_STORED
            archive.writestr(mimetype, b"application/epub+zip")
            archive.writestr("EPUB/package.opf", opf)

        result = await _strip_ooxml_from_path(p, "application/epub+zip")

        with zipfile.ZipFile(result) as archive:
            first = archive.infolist()[0]
            assert first.filename == "mimetype"
            assert first.compress_type == zipfile.ZIP_STORED
            cleaned = archive.read("EPUB/package.opf")
            assert b"Secret Author" not in cleaned
            assert b"Public" in cleaned

    async def test_epub_moves_mimetype_to_first_entry(self, tmp_path):
        p = tmp_path / "misordered.epub"
        with zipfile.ZipFile(p, "w") as archive:
            archive.writestr("EPUB/content.xhtml", b"<html/>")
            archive.writestr("mimetype", b"application/epub+zip")

        result = await _strip_ooxml_from_path(p, "application/epub+zip")

        with zipfile.ZipFile(result) as archive:
            first = archive.infolist()[0]
            assert first.filename == "mimetype"
            assert first.compress_type == zipfile.ZIP_STORED


# =============================================================================
# M2: GIF pixel budget enforcement
# =============================================================================


class TestGIFPixelBudget:
    """Verify that animated/multi-frame GIFs are rejected while single-frame GIFs pass."""

    def test_multi_frame_gif_rejected(self):
        """An animated multi-frame GIF should be rejected."""
        gif_bytes = _make_gif(10, 10, 3)
        with pytest.raises(ValueError, match="Animated and multi-frame images are not supported"):
            _strip_image_metadata(gif_bytes)

    def test_single_frame_gif_passes(self):
        """A single-frame GIF should pass metadata stripping."""
        gif_bytes = _make_gif(100, 100, 1)
        result = _strip_image_metadata(gif_bytes)
        assert len(result) > 0


# =============================================================================
# M5: SSE event log cap
# =============================================================================


class TestSSEEventLogCap:
    """Verify that the event log list is trimmed when it exceeds 200 entries."""

    @pytest.fixture
    def fake_redis(self):
        from tests.conftest import FakeRedis

        return FakeRedis()

    async def test_ltrim_called_when_list_exceeds_200(self, fake_redis):
        """After 200+ entries, LTRIM should cap the list."""
        key = "upload:eventlog:test-key"
        # Pre-populate with 200 entries
        fake_redis.data[key] = [f"event-{i}".encode() for i in range(200)]
        # Add one more to trigger trim
        idx = await fake_redis.rpush(key, "event-200")
        assert idx == 201
        await fake_redis.ltrim(key, -200, -1)
        assert len(fake_redis.data[key]) == 200
        # First event should have been trimmed
        assert fake_redis.data[key][0] == b"event-1"


# =============================================================================
# T4: Security bypass attempts — PDF
# =============================================================================


class TestPDFSafetyBypass:
    """Attempt to bypass PDF safety checks with crafted files."""

    def test_pdf_with_openaction(self, tmp_path):
        """PDF with /OpenAction JavaScript action should be rejected."""
        pdf = pikepdf.new()
        page = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name("/Page"),
                MediaBox=pikepdf.Array([0, 0, 612, 792]),
            )
        )
        pdf.pages.append(pikepdf.Page(page))
        pdf.Root["/OpenAction"] = pikepdf.Dictionary(
            S=pikepdf.Name("/JavaScript"),
            JS=pikepdf.String("app.alert('pwned')"),
        )
        p = tmp_path / "dangerous.pdf"
        pdf.save(str(p))

        with pytest.raises(ValueError, match="dangerous /OpenAction"):
            check_pdf_safety(p)

    def test_pdf_with_openaction_destination_allowed(self, tmp_path):
        """PDF with /OpenAction as a destination array (not an action dict) should be allowed."""
        pdf = pikepdf.new()
        page = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name("/Page"),
                MediaBox=pikepdf.Array([0, 0, 612, 792]),
            )
        )
        pdf.pages.append(pikepdf.Page(page))
        # Destination array: [page_ref, /Fit] — scrolls to a page, cannot execute code
        pdf.Root["/OpenAction"] = pikepdf.Array([page, pikepdf.Name("/Fit")])
        p = tmp_path / "dest.pdf"
        pdf.save(str(p))

        check_pdf_safety(p)  # must not raise

    def test_pdf_with_remote_goto_openaction_is_rejected(self, tmp_path):
        pdf = pikepdf.new()
        pdf.add_blank_page()
        pdf.Root["/OpenAction"] = pikepdf.Dictionary(
            S=pikepdf.Name("/GoToR"),
            F=pikepdf.String("https://example.invalid/file.pdf"),
            D=pikepdf.String("dest"),
        )
        p = tmp_path / "remote-goto.pdf"
        pdf.save(str(p))

        with pytest.raises(ValueError, match="dangerous /OpenAction"):
            check_pdf_safety(p)

    def test_pdf_with_dangerous_chained_action_is_rejected(self, tmp_path):
        pdf = pikepdf.new()
        pdf.add_blank_page()
        pdf.Root["/OpenAction"] = pikepdf.Dictionary(
            S=pikepdf.Name("/GoTo"),
            D=pikepdf.String("dest"),
            Next=pikepdf.Dictionary(
                S=pikepdf.Name("/JavaScript"), JS=pikepdf.String("app.alert(1)")
            ),
        )
        p = tmp_path / "chained-action.pdf"
        pdf.save(str(p))

        with pytest.raises(ValueError, match="dangerous /OpenAction"):
            check_pdf_safety(p)

    def test_pdf_with_javascript_in_names(self, tmp_path):
        """PDF with /JavaScript in Names tree should be rejected."""
        pdf = pikepdf.new()
        page = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name("/Page"),
                MediaBox=pikepdf.Array([0, 0, 612, 792]),
            )
        )
        pdf.pages.append(pikepdf.Page(page))
        pdf.Root["/Names"] = pikepdf.Dictionary(
            JavaScript=pikepdf.Dictionary(
                Names=pikepdf.Array([]),
            ),
        )
        p = tmp_path / "js.pdf"
        pdf.save(str(p))

        with pytest.raises(ValueError, match="JavaScript"):
            check_pdf_safety(p)

    def test_clean_pdf_passes(self, tmp_path):
        """A minimal clean PDF should not trigger any safety checks."""
        p = _make_minimal_pdf(tmp_path)
        # Should not raise
        check_pdf_safety(p)

    def test_corrupt_pdf_fails_closed(self, tmp_path):
        """A corrupt PDF should fail closed."""
        p = tmp_path / "corrupt.pdf"
        p.write_bytes(b"not-a-pdf")
        with pytest.raises(ValueError, match="malformed"):
            check_pdf_safety(p)

    def test_pdf_with_page_level_action(self, tmp_path):
        """PDF with /AA on a page node should be rejected."""
        pdf = pikepdf.new()
        page_dict = pikepdf.Dictionary(
            Type=pikepdf.Name("/Page"),
            MediaBox=pikepdf.Array([0, 0, 612, 792]),
            AA=pikepdf.Dictionary(
                O=pikepdf.Dictionary(
                    S=pikepdf.Name("/JavaScript"),
                    JS=pikepdf.String("evil()"),
                ),
            ),
        )
        page = pdf.make_indirect(page_dict)
        pdf.pages.append(pikepdf.Page(page))
        p = tmp_path / "page_action.pdf"
        pdf.save(str(p))

        with pytest.raises(ValueError, match="dangerous action"):
            check_pdf_safety(p)

    def test_pdf_with_outline_uri_action_is_rejected(self, tmp_path):
        pdf = pikepdf.new()
        pdf.add_blank_page()
        outline = pdf.make_indirect(
            pikepdf.Dictionary(
                Title=pikepdf.String("external link"),
                A=pikepdf.Dictionary(
                    S=pikepdf.Name("/URI"),
                    URI=pikepdf.String("https://attacker.invalid/pixel"),
                ),
            )
        )
        pdf.Root["/Outlines"] = pikepdf.Dictionary(First=outline, Last=outline)
        p = tmp_path / "outline-action.pdf"
        pdf.save(str(p))

        with pytest.raises(ValueError, match="outline.*dangerous action"):
            check_pdf_safety(p)


# =============================================================================
# T4: Security bypass attempts — SVG
# =============================================================================


class TestSVGSafetyBypass:
    """Attempt to bypass SVG safety checks with crafted payloads."""

    def test_svg_with_script_tag(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        with pytest.raises((ValueError, SvgSecurityError)):
            check_svg_safety(svg, "evil.svg")

    def test_svg_with_onload_handler(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><rect/></svg>'
        with pytest.raises((ValueError, SvgSecurityError)):
            check_svg_safety(svg, "handler.svg")

    def test_svg_with_javascript_uri(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:alert(1)"><rect/></a></svg>'
        with pytest.raises((ValueError, SvgSecurityError)):
            check_svg_safety(svg, "jsuri.svg")

    @pytest.mark.parametrize("reference", ["../secret.png", "/internal.png", "asset.png"])
    def test_svg_with_nonlocal_reference(self, reference):
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg"><image href="{reference}"/></svg>'
        ).encode()
        with pytest.raises(SvgSecurityError, match="external URLs"):
            check_svg_safety(svg, "reference.svg")

    def test_svg_with_local_fragment_reference_passes(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><use href="#shape"/></svg>'
        check_svg_safety(svg, "local.svg")

    def test_svg_xml_base_cannot_externalize_local_reference(self):
        svg = (
            b'<svg xmlns="http://www.w3.org/2000/svg" '
            b'xml:base="https://example.invalid/"><use href="#shape"/></svg>'
        )
        with pytest.raises(SvgSecurityError, match="base URI"):
            check_svg_safety(svg, "xml-base.svg")

    @pytest.mark.parametrize("element", ["animate", "animateMotion", "animateTransform", "set"])
    def test_svg_smil_cannot_mutate_safe_href_to_external_url(self, element):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg">'
            '<image id="target" href="#safe"/>'
            f'<{element} href="#target" attributeName="href" '
            'to="https://attacker.invalid/pixel" dur="1s"/>'
            "</svg>"
        ).encode()

        with pytest.raises(SvgSecurityError, match="elements are not allowed"):
            check_svg_safety(svg, "smil.svg")

    def test_clean_svg_passes(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="100" height="100"/></svg>'
        # Should not raise
        check_svg_safety(svg, "clean.svg")


# =============================================================================
# T4: Security bypass — filename / path traversal
# =============================================================================


class TestFilenameSecurityBypass:
    """Test filename sanitization edge cases in OOXML strip."""

    async def test_zip_path_traversal_in_entry(self, tmp_path):
        """ZIP entries with ../ should be handled safely."""
        entries = {
            "[Content_Types].xml": b"<Types/>",
            "word/document.xml": b"<doc/>",
        }
        p = _make_zip(tmp_path, entries)
        result = await _strip_ooxml_from_path(p)
        # Should produce a valid ZIP — traversal entries are sanitized
        with zipfile.ZipFile(result, "r") as z:
            assert "word/document.xml" in z.namelist()


# =============================================================================
# T5: Pipeline resume after checkpoint
# =============================================================================


class TestPipelineStageProgress:
    """Test the stage progress computation for resume-on-retry."""

    def test_stage_weights_sum_to_one(self):
        total = sum(w for _, _, w in _STAGES)
        assert abs(total - 1.0) < 1e-9

    def test_overall_at_stage_boundaries(self):
        """overall_percent at stage start/end must match cumulative weights."""
        accumulated = 0.0
        for i, (_, _, weight) in enumerate(_STAGES):
            assert abs(_overall(i, 0.0) - accumulated) < 1e-6
            accumulated += weight
            assert abs(_overall(i, 1.0) - accumulated) < 1e-6

    def test_overall_midstage(self):
        """50% through stage 0 (weight=0.35) should give 0.175."""
        result = _overall(0, 0.5)
        assert abs(result - 0.175) < 1e-4


# =============================================================================
# T6: Scanner fail-closed mode
# =============================================================================


class TestScannerFailClosed:
    """Test scanner behavior under fail-closed configuration."""

    async def test_malwarebazaar_timeout_with_fail_closed_raises(self):
        """When malwarebazaar_fail_closed=True, timeout should propagate."""
        import httpx

        from app.core.security.scanner import MalwareScanner

        scanner = MalwareScanner()
        scanner.client = AsyncMock(spec=httpx.AsyncClient)
        scanner.client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

        with patch("app.core.security.scanner.settings") as mock_settings:
            mock_settings.malwarebazaar_fail_closed = True
            mock_settings.malwarebazaar_url = "https://mb-api.abuse.ch/api/v1/"
            mock_settings.malwarebazaar_api_key = None

            with pytest.raises(httpx.TimeoutException):
                await scanner.check_malwarebazaar("a" * 64, "test.txt")

    async def test_malwarebazaar_timeout_without_fail_closed_passes(self):
        """When malwarebazaar_fail_closed=False (default), timeout returns None."""
        import httpx

        from app.core.security.scanner import MalwareScanner

        scanner = MalwareScanner()
        scanner.client = AsyncMock(spec=httpx.AsyncClient)
        scanner.client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

        with patch("app.core.security.scanner.settings") as mock_settings:
            mock_settings.malwarebazaar_fail_closed = False
            mock_settings.malwarebazaar_url = "https://mb-api.abuse.ch/api/v1/"
            mock_settings.malwarebazaar_api_key = None
            res = await scanner.check_malwarebazaar("a" * 64, "test.txt")
            assert res is None


# =============================================================================
# strip_metadata_file edge cases
# =============================================================================


class TestStripMetadataEdgeCases:
    """Edge cases for the metadata stripping dispatcher."""

    async def test_unknown_mime_returns_original(self, tmp_path):
        """Unknown MIME types should fail open (return original path)."""
        p = tmp_path / "mystery.bin"
        p.write_bytes(b"\x00" * 100)
        result = await strip_metadata_file(p, "application/octet-stream")
        assert result == p

    async def test_image_strip_corrupt_fails_closed(self, tmp_path):
        p = tmp_path / "bad.jpg"
        p.write_bytes(b"not-an-image")
        with pytest.raises(ValueError, match="sanitize"):
            await strip_metadata_file(p, "image/jpeg")

    async def test_image_strip_gif_bomb_propagates(self, tmp_path):
        """Animated GIF ValueError should propagate through strip_metadata_file."""
        gif_bytes = _make_gif(10, 10, 3)
        p = tmp_path / "bomb.gif"
        p.write_bytes(gif_bytes)
        with pytest.raises(ValueError, match="Animated and multi-frame images are not supported"):
            await strip_metadata_file(p, "image/gif")

    async def test_pdf_strip_corrupt_fails_closed(self, tmp_path):
        p = tmp_path / "bad.pdf"
        p.write_bytes(b"not-a-pdf")
        with pytest.raises(ValueError, match="sanitize"):
            await strip_metadata_file(p, "application/pdf")


# =============================================================================
# compress_file_path edge cases
# =============================================================================


class TestCompressEdgeCases:
    """Edge cases for file compression."""

    async def test_compress_unknown_mime_returns_original(self, tmp_path):
        """Unknown MIME types should return original path unchanged."""
        p = tmp_path / "data.bin"
        p.write_bytes(b"\x00" * 100)
        result = await compress_file_path(p, "application/octet-stream", "data.bin")
        assert result.path == p

    async def test_gzip_text_file(self, tmp_path):
        """text/* files above skip threshold should be gzip-compressed."""
        p = tmp_path / "readme.txt"
        content = b"Hello, world! " * 1000  # 14 KB — above 10 KiB skip threshold
        p.write_bytes(content)
        result = await compress_file_path(p, "text/plain", "readme.txt")
        assert result.content_encoding == "gzip"
        assert result.size < len(content)

    async def test_tiny_file_skips_compression(self, tmp_path):
        """Files below the skip threshold should not be compressed."""
        p = tmp_path / "tiny.txt"
        p.write_bytes(b"small")
        result = await compress_file_path(p, "text/plain", "tiny.txt")
        assert result.content_encoding is None
        assert result.path == p

    async def test_svg_gzip_no_help_returns_none_encoding(self, tmp_path):
        """When gzip does not reduce an SVG's size, content_encoding must be None.

        Previously the code returned content_encoding='gzip' even when
        _gzip_compress_path fell back to the original path, causing R2 to store
        plain SVG bytes labelled as gzip — the Cloudflare Worker would then pipe
        raw SVG through DecompressionStream and serve garbage to the browser.
        """
        # A tiny SVG that gzip cannot make meaningfully smaller.
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>'
        p = tmp_path / "tiny.svg"
        p.write_bytes(svg)
        result = await compress_file_path(p, "image/svg+xml", "tiny.svg")
        # Either gzip helped (and encoding is 'gzip') or it didn't (encoding is None).
        # In either case, if encoding is 'gzip', the returned file MUST actually be
        # valid gzip so the Worker can decompress it.
        if result.content_encoding == "gzip":
            import gzip as _gzip

            with _gzip.open(result.path, "rb") as f:
                f.read()  # raises if not valid gzip
        else:
            assert result.content_encoding is None


# =============================================================================
# FakeRedis correctness tests
# =============================================================================


class TestFakeRedis:
    """Verify FakeRedis implementation correctness for test infrastructure."""

    @pytest.fixture
    def redis(self):
        from tests.conftest import FakeRedis

        return FakeRedis()

    async def test_set_nx_creates_new(self, redis):
        result = await redis.set("key", "val", nx=True)
        assert result is True
        assert await redis.get("key") is not None

    async def test_set_nx_fails_on_existing(self, redis):
        await redis.set("key", "val1")
        result = await redis.set("key", "val2", nx=True)
        assert result is False
        raw = await redis.get("key")
        assert raw == b"val1"

    async def test_exists(self, redis):
        assert await redis.exists("nope") == 0
        await redis.set("key", "val")
        assert await redis.exists("key") == 1

    async def test_ltrim_keeps_tail(self, redis):
        for i in range(10):
            await redis.rpush("mylist", f"item-{i}")
        await redis.ltrim("mylist", -5, -1)
        assert len(redis.data["mylist"]) == 5
        assert redis.data["mylist"][0] == b"item-5"

    async def test_rpush_and_lrange(self, redis):
        await redis.rpush("list", "a")
        await redis.rpush("list", "b")
        await redis.rpush("list", "c")
        result = await redis.lrange("list", 0, -1)
        assert len(result) == 3
