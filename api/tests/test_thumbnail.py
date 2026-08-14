from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from PIL import Image, ImageDraw

from app.core.security.processing_paths import make_processing_temp_path
from app.workers.upload.stages.thumbnail import _is_blank_thumbnail


def _save(img: Image.Image, suffix: str = ".png") -> Path:
    path = make_processing_temp_path(suffix=suffix)
    img.save(path)
    return path


def _write_minimal_xlsx(path: Path) -> None:
    """Write a tiny, standards-compliant OOXML workbook without test dependencies."""
    members = {
        "[Content_Types].xml": """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>""",
        "_rels/.rels": """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
        "xl/workbook.xml": """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Courses" sheetId="1" r:id="rId1"/></sheets>
</workbook>""",
        "xl/_rels/workbook.xml.rels": """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>""",
        "xl/worksheets/sheet1.xml": """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1"><c r="A1" t="inlineStr"><is><t>Course</t></is></c><c r="B1" t="inlineStr"><is><t>Credits</t></is></c></row>
    <row r="2"><c r="A2" t="inlineStr"><is><t>Mathematics</t></is></c><c r="B2"><v>6</v></c></row>
  </sheetData>
</worksheet>""",
    }
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)


def test_all_white_is_blank() -> None:
    """A uniform white image has nothing to show → treated as blank."""
    img = Image.new("RGB", (200, 120), "white")
    assert _is_blank_thumbnail(_save(img)) is True


def test_white_page_with_text_is_not_blank() -> None:
    """A white page with dark content (text/figures) must NOT be discarded —
    this is the regression for PDFs whose thumbnail was wrongly dropped."""
    img = Image.new("RGB", (200, 120), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle([10, 10, 120, 60], fill="black")
    draw.text((10, 80), "Cours 2025", fill="black")
    assert _is_blank_thumbnail(_save(img)) is False


def test_pale_page_with_title_bar_is_not_blank() -> None:
    """A bright/pale page that still has a coloured title bar has real contrast
    and should be kept."""
    img = Image.new("RGB", (200, 120), (245, 245, 245))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 200, 25], fill=(40, 60, 120))
    assert _is_blank_thumbnail(_save(img)) is False


@pytest.mark.parametrize(
    "mime_type",
    [
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.oasis.opendocument.text",
        "application/vnd.oasis.opendocument.spreadsheet",
        "application/vnd.oasis.opendocument.presentation",
        "application/msword",
        "application/vnd.ms-excel",
        "application/vnd.ms-powerpoint",
    ],
)
def test_all_supported_office_families_share_the_converter(mime_type: str) -> None:
    from app.workers.upload.stages.thumbnail import _is_office_mime

    assert _is_office_mime(mime_type) is True


def test_soffice_command_bootstraps_private_libraries_without_proc_self() -> None:
    from app.workers.upload.stages.thumbnail import _soffice_command

    with patch(
        "app.workers.upload.stages.thumbnail.shutil.which",
        return_value="/opt/libreoffice/program/soffice",
    ):
        command = _soffice_command("--headless", "--version")

    assert command == [
        "/usr/bin/env",
        "LD_LIBRARY_PATH=/opt/libreoffice/program",
        "/opt/libreoffice/program/soffice",
        "--headless",
        "--version",
    ]


def test_thumbnail_repair_cli_uses_sandbox_bindable_temp_directory() -> None:
    import inspect

    from app.cli import _recalculate_thumbnails

    source = inspect.getsource(_recalculate_thumbnails)
    assert "processing_temp_dir" in source
    assert "tempfile.mkdtemp" not in source


@pytest.mark.asyncio
async def test_video_thumbnail_preserves_aspect_ratio_without_lossy_intermediate() -> None:
    from app.workers.upload.stages.thumbnail import _thumbnail_video

    input_path = make_processing_temp_path(suffix=".mp4")
    input_path.write_bytes(b"video")
    output_path = make_processing_temp_path(suffix=".webp")
    output_path.unlink()
    process = MagicMock(returncode=0, stdout=b"", stderr=b"")

    async def fake_run(command, **_kwargs):
        Image.new("RGB", (640, 360), "blue").save(Path(command[-1]))
        return process

    try:
        with (
            patch(
                "app.workers.upload.stages.thumbnail.async_sandboxed_run",
                side_effect=fake_run,
            ) as sandbox_run,
            patch(
                "app.workers.upload.stages.thumbnail._thumbnail_image",
                new_callable=AsyncMock,
            ) as render_image,
        ):
            await _thumbnail_video(input_path, output_path, (640, 640), 85)

        command = sandbox_run.call_args.args[0]
        assert "-s" not in command
        assert command[command.index("-vf") + 1] == (
            "scale=w=640:h=640:force_original_aspect_ratio=decrease"
        )
        assert Path(command[-1]).suffix == ".png"
        assert render_image.await_args.args[0].suffix == ".png"
    finally:
        input_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_pdf_thumbnail_raster_work_scales_with_requested_output() -> None:
    from app.workers.upload.stages.thumbnail import _thumbnail_pdf

    input_path = make_processing_temp_path(suffix=".pdf")
    input_path.write_bytes(b"%PDF-1.4\n")
    output_path = make_processing_temp_path(suffix=".webp")
    output_path.unlink()
    process = MagicMock(returncode=0, stdout=b"", stderr=b"")

    async def fake_run(command, **_kwargs):
        png_argument = next(value for value in command if value.startswith("-sOutputFile="))
        Image.new("RGB", (992, 1403), "white").save(Path(png_argument.split("=", 1)[1]))
        return process

    async def fake_render(_input, rendered_output, **_kwargs):
        rendered_output.write_bytes(b"webp")
        return False

    try:
        with (
            patch(
                "app.workers.upload.stages.thumbnail.async_sandboxed_run",
                side_effect=fake_run,
            ) as sandbox_run,
            patch(
                "app.workers.upload.stages.thumbnail.render_thumbnail_isolated",
                side_effect=fake_render,
            ),
        ):
            await _thumbnail_pdf(input_path, output_path, (640, 640), 85)

        command = sandbox_run.call_args.args[0]
        assert "-r116" in command
        assert "-dTextAlphaBits=4" in command
        assert "-dGraphicsAlphaBits=4" in command
        assert "-r150" not in command
    finally:
        input_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_sparse_single_page_pdf_keeps_first_page_when_second_is_missing() -> None:
    from app.workers.upload.stages.thumbnail import _thumbnail_pdf

    input_path = make_processing_temp_path(suffix=".pdf")
    input_path.write_bytes(b"%PDF-1.4\n")
    output_path = make_processing_temp_path(suffix=".webp")
    output_path.unlink()
    calls = 0

    async def fake_run(command, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            png_argument = next(value for value in command if value.startswith("-sOutputFile="))
            Image.new("RGB", (640, 640), "white").save(Path(png_argument.split("=", 1)[1]))
        return MagicMock(returncode=0, stdout=b"", stderr=b"")

    async def render_sparse_page(_input, rendered_output, **_kwargs):
        rendered_output.write_bytes(b"usable-first-page")
        return True

    try:
        with (
            patch(
                "app.workers.upload.stages.thumbnail.async_sandboxed_run",
                side_effect=fake_run,
            ),
            patch(
                "app.workers.upload.stages.thumbnail.render_thumbnail_isolated",
                side_effect=render_sparse_page,
            ),
        ):
            await _thumbnail_pdf(input_path, output_path, (640, 640), 85)

        assert calls == 2
        assert output_path.read_bytes() == b"usable-first-page"
    finally:
        input_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)


@pytest.mark.integration  # needs rsvg-convert
@pytest.mark.asyncio
async def test_run_thumbnail_stage_svg() -> None:
    """SVG files should be rendered to a WebP thumbnail via rsvg-convert."""
    from app.core.events.processing import ProcessingFile
    from app.workers.upload.stages.thumbnail import run_thumbnail_stage

    svg_content = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">'
        '<rect width="200" height="100" fill="#4a90d9"/>'
        '<text x="10" y="60" font-size="24" fill="white">Sample</text>'
        "</svg>"
    )
    temp_path = make_processing_temp_path(suffix=".svg")
    temp_path.write_text(svg_content)

    pf = ProcessingFile(temp_path, temp_path.stat().st_size)
    thumb_path_str = None
    try:
        thumb_path_str = await run_thumbnail_stage(pf, "image/svg+xml", "diagram.svg")
        assert thumb_path_str is not None
        thumb_path = Path(thumb_path_str)
        assert thumb_path.exists()
        assert thumb_path.suffix == ".webp"
        with Image.open(thumb_path) as img:
            assert img.format == "WEBP"
    finally:
        pf.cleanup()
        if thumb_path_str:
            Path(thumb_path_str).unlink(missing_ok=True)


@pytest.mark.integration  # needs soffice (libreoffice)
@pytest.mark.asyncio
async def test_run_thumbnail_stage_markdown() -> None:
    """Markdown files should be successfully converted to a WebP thumbnail."""
    from app.core.events.processing import ProcessingFile
    from app.workers.upload.stages.thumbnail import run_thumbnail_stage

    # Create a temp markdown file
    temp_path = make_processing_temp_path(suffix=".md")
    temp_path.write_text(
        "# Hello World\n\nThis is a sample markdown document to test thumbnail generation.\n\n- Bullet point 1\n- Bullet point 2\n"
    )

    pf = ProcessingFile(temp_path, temp_path.stat().st_size)
    thumb_path_str = None
    try:
        thumb_path_str = await run_thumbnail_stage(pf, "text/markdown", "test.md")
        assert thumb_path_str is not None
        thumb_path = Path(thumb_path_str)
        assert thumb_path.exists()
        assert thumb_path.suffix == ".webp"

        # Verify it's a valid image and not blank
        with Image.open(thumb_path) as img:
            assert img.format == "WEBP"

        assert _is_blank_thumbnail(thumb_path) is False
    finally:
        pf.cleanup()
        if thumb_path_str:
            Path(thumb_path_str).unlink(missing_ok=True)


@pytest.mark.integration  # needs soffice (libreoffice)
@pytest.mark.asyncio
async def test_run_thumbnail_stage_text() -> None:
    """Text/code files should be successfully converted to a WebP thumbnail."""
    from app.core.events.processing import ProcessingFile
    from app.workers.upload.stages.thumbnail import run_thumbnail_stage

    # Create a temp latex file
    temp_path = make_processing_temp_path(suffix=".tex")
    temp_path.write_text(
        "\\documentclass{article}\n\\begin{document}\nHello World from LaTeX!\n\\end{document}\n"
    )

    pf = ProcessingFile(temp_path, temp_path.stat().st_size)
    thumb_path_str = None
    try:
        thumb_path_str = await run_thumbnail_stage(pf, "text/x-tex", "test.tex")
        assert thumb_path_str is not None
        thumb_path = Path(thumb_path_str)
        assert thumb_path.exists()
        assert thumb_path.suffix == ".webp"

        # Verify it's a valid image and not blank
        with Image.open(thumb_path) as img:
            assert img.format == "WEBP"

        assert _is_blank_thumbnail(thumb_path) is False
    finally:
        pf.cleanup()
        if thumb_path_str:
            Path(thumb_path_str).unlink(missing_ok=True)


@pytest.mark.integration  # needs soffice (libreoffice) and Ghostscript
@pytest.mark.asyncio
async def test_run_thumbnail_stage_xlsx_from_extensionless_processing_path() -> None:
    """XLSX uploads retain a usable filename when sent to LibreOffice.

    Production downloads use extensionless processing paths, so exercising a
    convenient ``.xlsx`` temp path would miss the real upload failure.
    """
    from app.core.events.processing import ProcessingFile
    from app.workers.upload.stages.thumbnail import run_thumbnail_stage

    temp_path = make_processing_temp_path()
    _write_minimal_xlsx(temp_path)
    pf = ProcessingFile(temp_path, temp_path.stat().st_size)
    thumb_path_str = None
    try:
        thumb_path_str = await run_thumbnail_stage(
            pf,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "liste_courses.xlsx",
        )
        assert thumb_path_str is not None
        thumb_path = Path(thumb_path_str)
        with Image.open(thumb_path) as image:
            assert image.format == "WEBP"
            assert image.width <= 640
            assert image.height <= 640
        assert _is_blank_thumbnail(thumb_path) is False
    finally:
        pf.cleanup()
        if thumb_path_str:
            Path(thumb_path_str).unlink(missing_ok=True)


# ── New behaviour: raise on failure, None only for unsupported types ──────────


@pytest.mark.asyncio
async def test_run_thumbnail_stage_unsupported_mime_returns_none() -> None:
    """Unsupported MIME types return None without raising — no retry needed."""
    from app.core.events.processing import ProcessingFile
    from app.workers.upload.stages.thumbnail import run_thumbnail_stage

    temp_path = make_processing_temp_path(suffix=".bin")
    temp_path.write_bytes(b"\x00" * 64)

    pf = ProcessingFile(temp_path, 64)
    try:
        result = await run_thumbnail_stage(pf, "application/octet-stream", "file.bin")
        assert result is None, "Unsupported MIME type must return None, not raise"
    finally:
        pf.cleanup()


@pytest.mark.asyncio
async def test_run_thumbnail_stage_raises_on_generator_failure() -> None:
    """A failing generator now raises instead of silently returning None."""
    from app.core.events.processing import ProcessingFile
    from app.workers.upload.stages.thumbnail import run_thumbnail_stage

    temp_path = make_processing_temp_path(suffix=".jpg")
    temp_path.write_bytes(b"\xff\xd8\xff" + b"\x00" * 64)

    pf = ProcessingFile(temp_path, temp_path.stat().st_size)
    try:
        with patch(
            "app.workers.upload.stages.thumbnail._thumbnail_image",
            new_callable=AsyncMock,
            side_effect=RuntimeError("simulated Pillow failure"),
        ):
            with pytest.raises(RuntimeError, match="simulated Pillow failure"):
                await run_thumbnail_stage(pf, "image/jpeg", "photo.jpg")
    finally:
        pf.cleanup()


@pytest.mark.asyncio
async def test_run_thumbnail_stage_cleans_up_partial_file_on_failure() -> None:
    """The partial thumb file is deleted even when the generator raises mid-write."""
    from app.core.events.processing import ProcessingFile
    from app.workers.upload.stages.thumbnail import run_thumbnail_stage

    temp_path = make_processing_temp_path(suffix=".jpg")
    temp_path.write_bytes(b"\xff\xd8\xff" + b"\x00" * 64)

    pf = ProcessingFile(temp_path, temp_path.stat().st_size)
    expected_thumb = pf.path.parent / f"thumb_{pf.path.name}.webp"

    async def _write_then_fail(input_path, output_path, size, quality):
        output_path.write_bytes(b"partial")
        raise OSError("disk full")

    try:
        with patch(
            "app.workers.upload.stages.thumbnail._thumbnail_image",
            side_effect=_write_then_fail,
        ):
            with pytest.raises(OSError):
                await run_thumbnail_stage(pf, "image/jpeg", "photo.jpg")

        assert not expected_thumb.exists(), (
            "Partial thumb file must be deleted after a generator failure"
        )
    finally:
        pf.cleanup()
        expected_thumb.unlink(missing_ok=True)


# ── Regression tests: DOCX and Office thumbnail generation ───────────────────


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("Entretien Intervenant Kaizen.docx", True),
        ("notes.DOCX", True),
        ("sheet.xlsx", True),
        ("slides.pptx", True),
        ("legacy.doc", True),
        ("legacy.xls", True),
        ("legacy.ppt", True),
        ("document.odt", True),
        ("sheet.ods", True),
        ("presentation.odp", True),
        ("formatted.rtf", True),
        ("image.png", False),
        ("document.pdf", False),
        ("notes.txt", False),
    ],
)
def test_is_office_filename_detects_all_office_extensions(
    filename: str, expected: bool
) -> None:
    from app.workers.upload.stages.thumbnail import _is_office_filename

    assert _is_office_filename(filename) is expected


@pytest.mark.asyncio
async def test_thumbnail_office_creates_proper_suffix_and_runs_soffice() -> None:
    """_thumbnail_office copies extensionless inputs to a typed temp file before conversion."""
    from app.workers.upload.stages.thumbnail import _thumbnail_office

    input_path = make_processing_temp_path()
    input_path.write_bytes(b"dummy docx bytes")
    output_path = make_processing_temp_path(suffix=".webp")
    output_path.unlink(missing_ok=True)

    executed_cmd = None
    captured_rw_paths = None
    captured_ro_paths = None

    async def fake_run(cmd, ro_paths=None, rw_paths=None, **_kwargs):
        nonlocal executed_cmd, captured_rw_paths, captured_ro_paths
        executed_cmd = cmd
        captured_rw_paths = rw_paths
        captured_ro_paths = ro_paths
        tmp_dir = Path(rw_paths[0])
        pdf_out = tmp_dir / "document.pdf"
        pdf_out.write_bytes(b"%PDF-1.4\n")
        return MagicMock(returncode=0, stdout=b"", stderr=b"")

    try:
        with (
            patch(
                "app.workers.upload.stages.thumbnail.async_sandboxed_run",
                side_effect=fake_run,
            ),
            patch(
                "app.workers.upload.stages.thumbnail._thumbnail_pdf",
                new_callable=AsyncMock,
            ) as mock_pdf_thumb,
        ):
            await _thumbnail_office(input_path, output_path, (640, 640), 85, suffix=".docx")

            assert executed_cmd is not None
            assert executed_cmd[-1].endswith("document.docx")
            assert captured_ro_paths == []
            assert len(captured_rw_paths) == 1
            mock_pdf_thumb.assert_awaited_once()
            pdf_arg = mock_pdf_thumb.await_args.args[0]
            assert str(pdf_arg).endswith(".pdf")
    finally:
        input_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_run_thumbnail_stage_docx_routes_correctly() -> None:
    """DOCX files are routed to _thumbnail_office both with official and fallback MIME types."""
    from app.core.events.processing import ProcessingFile
    from app.workers.upload.stages.thumbnail import run_thumbnail_stage

    temp_path = make_processing_temp_path()
    temp_path.write_bytes(b"docx binary")
    pf = ProcessingFile(temp_path, temp_path.stat().st_size)

    try:
        with patch(
            "app.workers.upload.stages.thumbnail._thumbnail_office",
            new_callable=AsyncMock,
        ) as mock_office:

            async def fake_office(input_path, output_path, size, quality, suffix=".docx"):
                output_path.write_bytes(b"webp thumbnail")

            mock_office.side_effect = fake_office

            # 1. With official MIME type
            result = await run_thumbnail_stage(
                pf,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "Entretien Intervenant Kaizen.docx",
            )
            assert result is not None
            assert Path(result).exists()
            assert mock_office.call_args.kwargs.get("suffix") == ".docx"
            Path(result).unlink(missing_ok=True)

            # 2. With application/octet-stream MIME type (from sniffing heuristic)
            result2 = await run_thumbnail_stage(
                pf,
                "application/octet-stream",
                "Entretien Intervenant Kaizen.docx",
            )
            assert result2 is not None
            assert Path(result2).exists()
            assert mock_office.call_args.kwargs.get("suffix") == ".docx"
            Path(result2).unlink(missing_ok=True)
    finally:
        pf.cleanup()


def test_ipynb_to_markdown_extracts_markdown_code_and_plots() -> None:
    import base64
    import json
    from app.core.security.processing_paths import processing_temp_dir
    from app.workers.upload.stages.thumbnail import _ipynb_to_markdown

    tiny_png_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    nb = {
        "nbformat": 4,
        "metadata": {"language_info": {"name": "python"}},
        "cells": [
            {
                "cell_type": "markdown",
                "source": ["# Deep Learning\n", "Project notes."],
            },
            {
                "cell_type": "code",
                "execution_count": 1,
                "source": ["import torch\n", "print(torch.__version__)"],
                "outputs": [
                    {
                        "output_type": "display_data",
                        "data": {
                            "image/png": tiny_png_b64,
                            "text/plain": ["<Figure size 640x480>"],
                        },
                    },
                    {
                        "output_type": "stream",
                        "text": ["2.0.0+cu118\n"],
                    },
                ],
            },
        ],
    }
    raw_bytes = json.dumps(nb).encode("utf-8")
    with processing_temp_dir(prefix="test-nb-") as tmp_dir:
        md_text, first_img = _ipynb_to_markdown(raw_bytes, tmp_dir)
        assert "# Deep Learning" in md_text
        assert "```python\nimport torch" in md_text
        assert "![Output](output_1.png)" in md_text
        assert "> Output: `2.0.0+cu118`" in md_text
        assert first_img is not None
        assert first_img.exists()
        assert first_img.read_bytes() == base64.b64decode(tiny_png_b64)


@pytest.mark.asyncio
async def test_run_thumbnail_stage_ipynb_routes_correctly() -> None:
    """IPYNB files are routed to _thumbnail_ipynb."""
    from app.core.events.processing import ProcessingFile
    from app.workers.upload.stages.thumbnail import run_thumbnail_stage

    temp_path = make_processing_temp_path(suffix=".ipynb")
    temp_path.write_bytes(b'{"cells": []}')
    pf = ProcessingFile(temp_path, temp_path.stat().st_size)

    try:
        with patch(
            "app.workers.upload.stages.thumbnail._thumbnail_ipynb",
            new_callable=AsyncMock,
        ) as mock_ipynb:

            async def fake_ipynb(input_path, output_path, size, quality):
                output_path.write_bytes(b"webp thumbnail")

            mock_ipynb.side_effect = fake_ipynb

            result = await run_thumbnail_stage(
                pf,
                "application/json",
                "projet_deep_learning_l.ipynb",
            )
            assert result is not None
            assert Path(result).exists()
            assert mock_ipynb.called
            Path(result).unlink(missing_ok=True)
    finally:
        pf.cleanup()


@pytest.mark.asyncio
async def test_thumbnail_ipynb_calls_soffice_and_thumbnail_pdf() -> None:
    """_thumbnail_ipynb writes markdown and calls soffice/pdf pipeline."""
    import json
    from app.workers.upload.stages.thumbnail import _thumbnail_ipynb

    nb = {
        "nbformat": 4,
        "metadata": {"language_info": {"name": "python"}},
        "cells": [
            {"cell_type": "markdown", "source": "# Test Notebook"},
            {"cell_type": "code", "execution_count": 1, "source": "x = 10", "outputs": []},
        ],
    }
    input_path = make_processing_temp_path(suffix=".ipynb")
    input_path.write_text(json.dumps(nb), encoding="utf-8")
    output_path = make_processing_temp_path(suffix=".webp")

    try:
        with (
            patch(
                "app.workers.upload.stages.thumbnail.async_sandboxed_run",
                new_callable=AsyncMock,
            ) as mock_run,
            patch(
                "app.workers.upload.stages.thumbnail._thumbnail_pdf",
                new_callable=AsyncMock,
            ) as mock_thumb_pdf,
        ):
            async def fake_sandboxed_run(cmd, ro_paths, rw_paths, timeout=60):
                # Simulate LibreOffice creating document.pdf in rw_paths[0]
                tmp_dir = rw_paths[0]
                (tmp_dir / "document.pdf").write_bytes(b"%PDF-1.4 mock")
                mock_proc = MagicMock()
                mock_proc.returncode = 0
                mock_proc.stdout = b""
                mock_proc.stderr = b""
                return mock_proc

            async def fake_pdf(pdf_path, out_path, size, quality):
                out_path.write_bytes(b"webp image data")

            mock_run.side_effect = fake_sandboxed_run
            mock_thumb_pdf.side_effect = fake_pdf

            await _thumbnail_ipynb(input_path, output_path, (640, 640), 85)
            assert mock_run.called
            assert mock_thumb_pdf.called
            assert output_path.exists()
            assert output_path.read_bytes() == b"webp image data"
    finally:
        input_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_thumbnail_ipynb_falls_back_to_embedded_plot_if_soffice_fails() -> None:
    """If LibreOffice conversion fails, fallback to rendering the extracted plot image."""
    import json
    from app.workers.upload.stages.thumbnail import _thumbnail_ipynb

    tiny_png_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    nb = {
        "nbformat": 4,
        "metadata": {"language_info": {"name": "python"}},
        "cells": [
            {
                "cell_type": "code",
                "execution_count": 1,
                "source": "plt.plot([1, 2])",
                "outputs": [
                    {
                        "output_type": "display_data",
                        "data": {"image/png": tiny_png_b64},
                    }
                ],
            }
        ],
    }
    input_path = make_processing_temp_path(suffix=".ipynb")
    input_path.write_text(json.dumps(nb), encoding="utf-8")
    output_path = make_processing_temp_path(suffix=".webp")

    try:
        with (
            patch(
                "app.workers.upload.stages.thumbnail.async_sandboxed_run",
                new_callable=AsyncMock,
            ) as mock_run,
            patch(
                "app.workers.upload.stages.thumbnail._thumbnail_image",
                new_callable=AsyncMock,
            ) as mock_thumb_img,
        ):
            # Simulate LibreOffice failing (no PDF generated)
            mock_proc = MagicMock()
            mock_proc.returncode = 1
            mock_proc.stdout = b""
            mock_proc.stderr = b"soffice error"
            mock_run.return_value = mock_proc

            async def fake_img(img_path, out_path, size, quality):
                out_path.write_bytes(b"webp image from fallback")

            mock_thumb_img.side_effect = fake_img

            await _thumbnail_ipynb(input_path, output_path, (640, 640), 85)
            assert mock_thumb_img.called
            assert output_path.exists()
            assert output_path.read_bytes() == b"webp image from fallback"
    finally:
        input_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)


@pytest.mark.integration  # needs soffice (libreoffice) and Ghostscript with bwrap namespaces
@pytest.mark.asyncio
async def test_run_thumbnail_stage_ipynb_e2e_renders_webp() -> None:
    """Real end-to-end rendering of a Jupyter notebook to WebP."""
    import json
    from app.core.events.processing import ProcessingFile
    from app.workers.upload.stages.thumbnail import run_thumbnail_stage

    tiny_png_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    nb = {
        "nbformat": 4,
        "metadata": {"language_info": {"name": "python"}},
        "cells": [
            {
                "cell_type": "markdown",
                "source": ["# Analysis Report\n", "Introductory summary."],
            },
            {
                "cell_type": "code",
                "execution_count": 1,
                "source": ["import numpy as np\n", "print('done')"],
                "outputs": [
                    {
                        "output_type": "display_data",
                        "data": {"image/png": tiny_png_b64},
                    }
                ],
            },
        ],
    }
    temp_path = make_processing_temp_path(suffix=".ipynb")
    temp_path.write_text(json.dumps(nb), encoding="utf-8")
    pf = ProcessingFile(temp_path, temp_path.stat().st_size)

    try:
        result = await run_thumbnail_stage(
            pf,
            "application/json",
            "notebook.ipynb",
        )
        assert result is not None
        result_path = Path(result)
        assert result_path.exists()
        assert result_path.stat().st_size > 0
        # Verify it is a valid image
        with Image.open(result_path) as img:
            assert img.format == "WEBP"
        result_path.unlink(missing_ok=True)
    finally:
        pf.cleanup()


