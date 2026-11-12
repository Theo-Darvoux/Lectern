import shutil
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from PIL import Image, ImageDraw
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security.parser_child import is_blank_image
from app.core.security.processing_paths import make_processing_temp_path

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


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
    assert is_blank_image(img) is True


def test_white_page_with_text_is_not_blank() -> None:
    """A white page with dark content (text/figures) must NOT be discarded —
    this is the regression for PDFs whose thumbnail was wrongly dropped."""
    img = Image.new("RGB", (200, 120), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle([10, 10, 120, 60], fill="black")
    draw.text((10, 80), "Cours 2025", fill="black")
    assert is_blank_image(img) is False


def test_pale_page_with_title_bar_is_not_blank() -> None:
    """A bright/pale page that still has a coloured title bar has real contrast
    and should be kept."""
    img = Image.new("RGB", (200, 120), (245, 245, 245))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 200, 25], fill=(40, 60, 120))
    assert is_blank_image(img) is False


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


@pytest.mark.asyncio
async def test_recalculate_thumbnails_cli_dispatch(db_session: AsyncSession) -> None:
    import uuid

    from arq.jobs import JobStatus

    from app.cli import _recalculate_thumbnails
    from app.models.directory import Directory
    from app.models.material import Material, MaterialVersion
    from app.models.user import User, UserRole

    user = User(
        id=uuid.uuid4(),
        email="cli_recalc@example.com",
        display_name="CLI Tester",
        role=UserRole.STUDENT,
        onboarded=True,
        gdpr_consent=True,
    )
    db_session.add(user)
    await db_session.flush()

    directory = Directory(
        id=uuid.uuid4(),
        name="CLI Dir",
        slug="cli-dir",
        type="folder",
        created_by=user.id,
    )
    db_session.add(directory)
    await db_session.flush()

    material = Material(
        id=uuid.uuid4(),
        directory_id=directory.id,
        title="CLI Material",
        slug="cli-material",
        type="pdf",
        author_id=user.id,
        current_version=1,
    )
    db_session.add(material)
    await db_session.flush()

    version = MaterialVersion(
        id=uuid.uuid4(),
        material_id=material.id,
        version_number=1,
        file_key="uploads/test/cli.pdf",
        file_name="cli.pdf",
        file_size=2048,
        file_mime_type="application/pdf",
        thumbnail_status="failed",
    )
    db_session.add(version)
    await db_session.commit()

    mock_job = AsyncMock()
    mock_job.job_id = "job-123"
    mock_job.status = AsyncMock(return_value=JobStatus.complete)
    mock_result_info = MagicMock()
    mock_result_info.success = True
    mock_result_info.result = "ok"
    mock_job.result_info = AsyncMock(return_value=mock_result_info)

    mock_pool = AsyncMock()
    mock_pool.enqueue_job = AsyncMock(return_value=mock_job)

    with (
        patch("app.core.database.redis.init_arq_pool", new_callable=AsyncMock),
        patch("app.core.database.redis.close_arq_pool", new_callable=AsyncMock),
        patch("app.core.database.redis.arq_pool", mock_pool),
    ):
        await _recalculate_thumbnails(
            batch_size=10,
            max_concurrency=5,
            limit=None,
            dry_run=False,
            force=False,
        )

        mock_pool.enqueue_job.assert_awaited_once()
        kwargs = mock_pool.enqueue_job.await_args.kwargs
        assert kwargs["material_id"] == str(material.id)
        assert kwargs["version_id"] == str(version.id)


@pytest.mark.asyncio
async def test_pdf_thumbnail_raster_work_scales_with_requested_output() -> None:
    from app.workers.upload.stages.thumbnail import _thumbnail_pdf

    input_path = make_processing_temp_path(suffix=".pdf")
    input_path.write_bytes(b"%PDF-1.4\n")
    output_path = make_processing_temp_path(suffix=".webp")
    output_path.unlink()
    process = MagicMock(returncode=0, stdout=b"", stderr=b"")

    async def fake_run(command, **_kwargs):
        Image.new("RGB", (679, 960), "white").save(Path(f"{command[-1]}.png"))
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
        assert command[0] == "pdftoppm"
        assert "-png" in command
        assert "-singlefile" in command
        # The long side is bounded at 1.5x the target, whatever the page size.
        assert command[command.index("-scale-to") + 1] == "960"
        assert "-r" not in command
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
            Image.new("RGB", (640, 640), "white").save(Path(f"{command[-1]}.png"))
            return MagicMock(returncode=0, stdout=b"", stderr=b"")
        return MagicMock(returncode=99, stdout=b"", stderr=b"Page not found")

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
@pytest.mark.skipif(shutil.which("rsvg-convert") is None, reason="rsvg-convert not installed")
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
            # A two-row sheet sits near the blank heuristic's cutoff, so look for ink.
            assert image.convert("L").getextrema()[0] < 128
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
    expected_thumb = pf.path.with_name(f"{pf.path.name}.thumb.webp")

    async def _write_then_fail(input_path, output_path, size, quality, mime):
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
                "app.workers.upload.stages.thumbnail.shutil.which",
                return_value="/opt/libreoffice/program/soffice",
            ),
            patch(
                "app.workers.upload.stages.thumbnail.async_sandboxed_run",
                side_effect=fake_run,
            ),
            patch(
                "app.workers.upload.stages.thumbnail._thumbnail_pdf",
                new_callable=AsyncMock,
            ) as mock_pdf_thumb,
        ):
            await _thumbnail_office(
                input_path,
                output_path,
                (640, 640),
                85,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )

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
    """DOCX files reach _thumbnail_office with their MIME type, or via the extension
    when the stored type is generic."""
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

            async def fake_office(input_path, output_path, size, quality, mime):
                output_path.write_bytes(b"webp thumbnail")
                return True

            mock_office.side_effect = fake_office

            # 1. With official MIME type
            result = await run_thumbnail_stage(
                pf,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "Entretien Intervenant Kaizen.docx",
            )
            assert result is not None
            assert Path(result).exists()
            assert mock_office.call_args.args[4] == DOCX_MIME
            Path(result).unlink(missing_ok=True)

            # 2. With application/octet-stream MIME type (from sniffing heuristic)
            result2 = await run_thumbnail_stage(
                pf,
                "application/octet-stream",
                "Entretien Intervenant Kaizen.docx",
            )
            assert result2 is not None
            assert Path(result2).exists()
            assert mock_office.call_args.args[4] == DOCX_MIME
            Path(result2).unlink(missing_ok=True)
    finally:
        pf.cleanup()


@pytest.mark.asyncio
async def test_recalculate_thumbnail_worker_success(db_session: AsyncSession) -> None:
    import uuid
    from contextlib import asynccontextmanager

    from app.models.directory import Directory
    from app.models.material import Material, MaterialVersion
    from app.models.user import User, UserRole
    from app.workers.recalculate_thumbnail import recalculate_thumbnail

    user = User(
        id=uuid.uuid4(),
        email="recalc@example.com",
        display_name="Recalc Tester",
        role=UserRole.STUDENT,
        onboarded=True,
        gdpr_consent=True,
    )
    db_session.add(user)
    await db_session.flush()

    directory = Directory(
        id=uuid.uuid4(),
        name="Dir",
        slug="dir",
        type="folder",
        created_by=user.id,
    )
    db_session.add(directory)
    await db_session.flush()

    material = Material(
        id=uuid.uuid4(),
        directory_id=directory.id,
        title="Recalc Material",
        slug="recalc-material",
        type="image",
        author_id=user.id,
        current_version=1,
    )
    db_session.add(material)
    await db_session.flush()

    version = MaterialVersion(
        id=uuid.uuid4(),
        material_id=material.id,
        version_number=1,
        file_key="uploads/test/image.png",
        file_name="image.png",
        file_size=1024,
        file_mime_type="image/png",
    )
    db_session.add(version)
    await db_session.commit()

    @asynccontextmanager
    async def mock_sessionmaker():
        yield db_session

    ctx = {"db_sessionmaker": mock_sessionmaker}

    mock_thumb_file = make_processing_temp_path(suffix=".webp")
    with Image.new("RGB", (100, 100), color="blue") as img:
        img.save(mock_thumb_file, format="WEBP")

    async def _fake_download(_key: str, dest_path: Path, **_kwargs: Any) -> None:
        dest_path.write_bytes(b"dummy content")

    with (
        patch(
            "app.workers.recalculate_thumbnail.download_file", side_effect=_fake_download
        ) as mock_download,
        patch(
            "app.workers.recalculate_thumbnail.run_thumbnail_stage", new_callable=AsyncMock
        ) as mock_stage,
        patch(
            "app.workers.recalculate_thumbnail.upload_file", new_callable=AsyncMock
        ) as mock_upload,
    ):
        mock_stage.return_value = str(mock_thumb_file)
        result = await recalculate_thumbnail(ctx, str(material.id))
        assert result == "ok"

        assert mock_download.call_count == 1
        mock_stage.assert_awaited_once()
        mock_upload.assert_awaited_once()
        uploaded_key = mock_upload.await_args.args[1]
        prefix = f"thumbnails/{version.id}/"
        assert uploaded_key.startswith(prefix)
        assert uploaded_key.endswith(".webp")

        # Regenerated thumbnails use a fresh immutable object key so an edge cache
        # can never serve bytes from an older regeneration of the same version.
        generation_id = uploaded_key[len(prefix) : -len(".webp")]
        assert uuid.UUID(hex=generation_id).hex == generation_id
        assert mock_upload.await_args.kwargs["content_type"] == "image/webp"

    await db_session.refresh(version)
    assert version.thumbnail_key == uploaded_key
    assert version.thumbnail_status == "ok"


@pytest.mark.asyncio
async def test_recalculate_thumbnail_worker_skipped_and_failed(db_session: AsyncSession) -> None:
    import uuid
    from contextlib import asynccontextmanager

    from app.models.directory import Directory
    from app.models.material import Material, MaterialVersion
    from app.models.user import User, UserRole
    from app.workers.recalculate_thumbnail import recalculate_thumbnail

    user = User(
        id=uuid.uuid4(),
        email="recalc2@example.com",
        display_name="Recalc Tester 2",
        role=UserRole.STUDENT,
        onboarded=True,
        gdpr_consent=True,
    )
    db_session.add(user)
    await db_session.flush()

    directory = Directory(
        id=uuid.uuid4(),
        name="Dir 2",
        slug="dir-2",
        type="folder",
        created_by=user.id,
    )
    db_session.add(directory)
    await db_session.flush()

    material = Material(
        id=uuid.uuid4(),
        directory_id=directory.id,
        title="Binary Material",
        slug="binary-material",
        type="binary",
        author_id=user.id,
        current_version=1,
    )
    db_session.add(material)
    await db_session.flush()

    version = MaterialVersion(
        id=uuid.uuid4(),
        material_id=material.id,
        version_number=1,
        file_key="uploads/test/binary.bin",
        file_name="binary.bin",
        file_size=1024,
        file_mime_type="application/octet-stream",
        thumbnail_key=f"thumbnails/{'a' * 64}/old-upload.webp",
        thumbnail_status="ok",
    )
    db_session.add(version)
    await db_session.commit()

    @asynccontextmanager
    async def mock_sessionmaker():
        yield db_session

    ctx = {"db_sessionmaker": mock_sessionmaker}

    async def _fake_download(_key: str, dest_path: Path, **_kwargs: Any) -> None:
        dest_path.write_bytes(b"dummy binary")

    # Case 1: run_thumbnail_stage returns None -> skipped
    with (
        patch("app.workers.recalculate_thumbnail.download_file", side_effect=_fake_download),
        patch(
            "app.workers.recalculate_thumbnail._delete_thumbnail_if_unreferenced",
            new_callable=AsyncMock,
        ) as retire,
        patch(
            "app.workers.recalculate_thumbnail.run_thumbnail_stage", new_callable=AsyncMock
        ) as mock_stage,
    ):
        mock_stage.return_value = None
        result = await recalculate_thumbnail(ctx, str(material.id))
        assert result == "skipped"

    await db_session.refresh(version)
    assert version.thumbnail_status == "skipped"
    # A file that no longer gets a thumbnail loses its old one.
    assert version.thumbnail_key is None
    retire.assert_awaited_once()
    assert retire.await_args.args[1] == f"thumbnails/{'a' * 64}/old-upload.webp"

    # Case 2: exception raised -> failed
    with (
        patch(
            "app.workers.recalculate_thumbnail.download_file",
            side_effect=RuntimeError("Storage error"),
        ),
    ):
        result = await recalculate_thumbnail(ctx, str(material.id))
        assert result == "failed"

    await db_session.refresh(version)
    assert version.thumbnail_status == "failed"


# ── File classification ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("mime_type", "file_name", "expected"),
    [
        ("image/png", "a.png", "image"),
        ("image/gif", "a.gif", "image"),
        ("image/svg+xml", "a.svg", "svg"),
        ("image/vnd.djvu", "a.djvu", "djvu"),
        ("video/mp4", "a.mp4", "video"),
        ("audio/mpeg", "a.mp3", "audio"),
        ("application/pdf", "a.pdf", "pdf"),
        ("application/epub+zip", "a.epub", "epub"),
        (DOCX_MIME, "a.docx", "office"),
        ("application/vnd.ms-powerpoint", "a.ppt", "office"),
        ("application/vnd.oasis.opendocument.spreadsheet", "a.ods", "office"),
        # Generic stored types fall back to the extension.
        ("application/octet-stream", "a.docx", "office"),
        ("", "a.pdf", "pdf"),
        # Text formats show a generic card instead.
        ("text/plain", "a.txt", None),
        ("text/markdown", "a.md", None),
        ("text/x-python", "a.py", None),
        ("application/json", "a.ipynb", None),
        ("application/json", "a.json", None),
        ("application/octet-stream", "a.bin", None),
        # A detected type wins over a misleading extension.
        ("text/plain", "fake.png", None),
    ],
)
def test_thumbnail_kind(mime_type: str, file_name: str, expected: str | None) -> None:
    from app.core.media.thumbnails import thumbnail_kind

    kind = thumbnail_kind(mime_type, file_name)
    assert (kind.value if kind else None) == expected


@pytest.mark.parametrize(
    ("mime_type", "expected"),
    [
        ("image/jpeg", True),
        ("image/svg+xml", True),
        ("video/webm", True),
        ("application/pdf", True),
        ("image/vnd.djvu", False),
        ("audio/mpeg", False),
        (DOCX_MIME, False),
        ("text/plain", False),
    ],
)
def test_native_preview_only_for_browser_renderable_types(mime_type: str, expected: bool) -> None:
    from app.core.media.thumbnails import has_native_preview

    assert has_native_preview(mime_type) is expected


@pytest.mark.asyncio
async def test_run_thumbnail_stage_skips_text_files_without_rendering() -> None:
    from app.core.events.processing import ProcessingFile
    from app.workers.upload.stages.thumbnail import run_thumbnail_stage

    temp_path = make_processing_temp_path(suffix=".py")
    temp_path.write_text("print('hello')\n")
    pf = ProcessingFile(temp_path, temp_path.stat().st_size)
    try:
        with patch("app.workers.upload.stages.thumbnail.async_sandboxed_run") as sandbox_run:
            assert await run_thumbnail_stage(pf, "text/x-python", "solver.py") is None
        sandbox_run.assert_not_called()
    finally:
        pf.cleanup()


@pytest.mark.asyncio
async def test_run_thumbnail_stage_returns_none_when_renderer_finds_nothing() -> None:
    from app.core.events.processing import ProcessingFile
    from app.workers.upload.stages.thumbnail import run_thumbnail_stage

    temp_path = make_processing_temp_path(suffix=".mp3")
    temp_path.write_bytes(b"ID3")
    pf = ProcessingFile(temp_path, temp_path.stat().st_size)
    try:
        with patch(
            "app.workers.upload.stages.thumbnail._thumbnail_av",
            new_callable=AsyncMock,
            return_value=False,
        ):
            assert await run_thumbnail_stage(pf, "audio/mpeg", "song.mp3") is None
        assert not pf.path.with_name(f"{pf.path.name}.thumb.webp").exists()
    finally:
        pf.cleanup()


# ── PDF ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pdf_page_two_render_failure_keeps_blank_page_one() -> None:
    from app.workers.upload.stages.thumbnail import _thumbnail_pdf

    input_path = make_processing_temp_path(suffix=".pdf")
    input_path.write_bytes(b"%PDF-1.4\n")
    output_path = make_processing_temp_path(suffix=".webp")
    output_path.unlink()

    async def fake_run(command, **_kwargs):
        Image.new("RGB", (640, 640), "white").save(Path(f"{command[-1]}.png"))
        return MagicMock(returncode=0, stdout=b"", stderr=b"")

    async def render(source, rendered_output, **_kwargs):
        if source.name == "page-2.png":
            rendered_output.write_bytes(b"partial")
            raise ValueError("decompression bomb")
        rendered_output.write_bytes(b"page-one")
        return True  # blank: page 2 is tried

    try:
        with (
            patch(
                "app.workers.upload.stages.thumbnail.async_sandboxed_run",
                side_effect=fake_run,
            ),
            patch(
                "app.workers.upload.stages.thumbnail.render_thumbnail_isolated",
                side_effect=render,
            ),
        ):
            assert await _thumbnail_pdf(input_path, output_path, (640, 640), 85) is True
        assert output_path.read_bytes() == b"page-one"
    finally:
        input_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_pdf_uses_non_blank_page_two() -> None:
    from app.workers.upload.stages.thumbnail import _thumbnail_pdf

    input_path = make_processing_temp_path(suffix=".pdf")
    input_path.write_bytes(b"%PDF-1.4\n")
    output_path = make_processing_temp_path(suffix=".webp")
    output_path.unlink()

    async def fake_run(command, **_kwargs):
        Image.new("RGB", (640, 640), "white").save(Path(f"{command[-1]}.png"))
        return MagicMock(returncode=0, stdout=b"", stderr=b"")

    async def render(source, rendered_output, **_kwargs):
        rendered_output.write_bytes(source.stem.encode())
        return source.name == "page-1.png"

    try:
        with (
            patch(
                "app.workers.upload.stages.thumbnail.async_sandboxed_run",
                side_effect=fake_run,
            ),
            patch(
                "app.workers.upload.stages.thumbnail.render_thumbnail_isolated",
                side_effect=render,
            ),
        ):
            await _thumbnail_pdf(input_path, output_path, (640, 640), 85)
        assert output_path.read_bytes() == b"page-2"
    finally:
        input_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_pdf_first_page_render_failure_raises_render_error() -> None:
    from app.workers.upload.stages.thumbnail import ThumbnailRenderError, _thumbnail_pdf

    input_path = make_processing_temp_path(suffix=".pdf")
    input_path.write_bytes(b"not a pdf")
    output_path = make_processing_temp_path(suffix=".webp")
    output_path.unlink()
    try:
        with patch(
            "app.workers.upload.stages.thumbnail.async_sandboxed_run",
            new_callable=AsyncMock,
            return_value=MagicMock(returncode=1, stdout=b"", stderr=b"Syntax Error"),
        ):
            with pytest.raises(ThumbnailRenderError):
                await _thumbnail_pdf(input_path, output_path, (640, 640), 85)
    finally:
        input_path.unlink(missing_ok=True)


# ── Office ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_office_falls_back_to_embedded_image_when_converted_pdf_fails() -> None:
    from app.workers.upload.stages.thumbnail import ThumbnailRenderError, _thumbnail_office

    input_path = make_processing_temp_path()
    input_path.write_bytes(b"docx")
    output_path = make_processing_temp_path(suffix=".webp")
    output_path.unlink()

    async def fake_run(cmd, rw_paths=None, **_kwargs):
        (Path(rw_paths[0]) / "document.pdf").write_bytes(b"%PDF-1.4\n")
        return MagicMock(returncode=0, stdout=b"", stderr=b"")

    try:
        with (
            patch(
                "app.workers.upload.stages.thumbnail.shutil.which",
                return_value="/usr/bin/soffice",
            ),
            patch(
                "app.workers.upload.stages.thumbnail.async_sandboxed_run",
                side_effect=fake_run,
            ),
            patch(
                "app.workers.upload.stages.thumbnail._thumbnail_pdf",
                new_callable=AsyncMock,
                side_effect=ThumbnailRenderError("pdftoppm failed"),
            ),
            patch(
                "app.workers.upload.stages.thumbnail.extract_office_thumbnail_isolated",
                new_callable=AsyncMock,
                return_value=True,
            ) as embedded,
        ):
            assert await _thumbnail_office(input_path, output_path, (640, 640), 85, DOCX_MIME)
        embedded.assert_awaited_once()
    finally:
        input_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_office_without_any_render_raises_render_error() -> None:
    from app.workers.upload.stages.thumbnail import ThumbnailRenderError, _thumbnail_office

    input_path = make_processing_temp_path()
    input_path.write_bytes(b"ole2")
    output_path = make_processing_temp_path(suffix=".webp")
    output_path.unlink()
    try:
        with (
            patch(
                "app.workers.upload.stages.thumbnail.shutil.which",
                return_value="/usr/bin/soffice",
            ),
            patch(
                "app.workers.upload.stages.thumbnail.async_sandboxed_run",
                new_callable=AsyncMock,
                return_value=MagicMock(returncode=1, stdout=b"", stderr=b"crash"),
            ),
            patch(
                "app.workers.upload.stages.thumbnail.extract_office_thumbnail_isolated",
                new_callable=AsyncMock,
                side_effect=ValueError("File is not a zip file"),
            ),
        ):
            with pytest.raises(ThumbnailRenderError):
                await _thumbnail_office(
                    input_path, output_path, (640, 640), 85, "application/msword"
                )
    finally:
        input_path.unlink(missing_ok=True)


# ── Video and audio ────────────────────────────────────────────────────────────


def _ffprobe_result(streams: list[dict[str, Any]]) -> MagicMock:
    import json

    return MagicMock(returncode=0, stdout=json.dumps({"streams": streams}).encode(), stderr=b"")


async def _run_av(
    mime: str, streams: list[dict[str, Any]], frame_from_seek: str | None = None
) -> tuple[bool, list[list[str]]]:
    from app.workers.upload.stages.thumbnail import _thumbnail_av

    input_path = make_processing_temp_path()
    input_path.write_bytes(b"media")
    output_path = make_processing_temp_path(suffix=".webp")
    output_path.unlink()
    commands: list[list[str]] = []

    async def fake_run(command, **_kwargs):
        commands.append(command)
        if command[0] == "ffprobe":
            return _ffprobe_result(streams)
        seek = command[command.index("-ss") + 1] if "-ss" in command else None
        if frame_from_seek == "any" or seek == frame_from_seek:
            Image.new("RGB", (640, 360), "blue").save(Path(command[-1]))
        return MagicMock(returncode=0, stdout=b"", stderr=b"")

    try:
        with (
            patch(
                "app.workers.upload.stages.thumbnail.async_sandboxed_run",
                side_effect=fake_run,
            ),
            patch(
                "app.workers.upload.stages.thumbnail.render_thumbnail_isolated",
                new_callable=AsyncMock,
                return_value=False,
            ),
        ):
            produced = await _thumbnail_av(input_path, output_path, (640, 640), 85, mime)
        return produced, commands
    finally:
        input_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_video_frame_keeps_aspect_ratio_and_lossless_intermediate() -> None:
    produced, commands = await _run_av(
        "video/mp4", [{"index": 0, "disposition": {"attached_pic": 0}}], frame_from_seek="2"
    )
    assert produced is True
    ffmpeg = commands[1]
    assert ffmpeg[ffmpeg.index("-ss") + 1] == "2"
    assert ffmpeg[ffmpeg.index("-map") + 1] == "0:0"
    assert ffmpeg[ffmpeg.index("-vf") + 1] == (
        "scale=w=640:h=640:force_original_aspect_ratio=decrease"
    )
    assert Path(ffmpeg[-1]).suffix == ".png"


@pytest.mark.asyncio
async def test_short_video_falls_back_to_first_frame() -> None:
    produced, commands = await _run_av(
        "video/webm", [{"index": 0, "disposition": {"attached_pic": 0}}], frame_from_seek="0"
    )
    assert produced is True
    assert [c[c.index("-ss") + 1] for c in commands[1:]] == ["2", "0"]


@pytest.mark.asyncio
async def test_video_prefers_a_real_frame_over_embedded_cover() -> None:
    streams = [
        {"index": 0, "disposition": {"attached_pic": 0}},
        {"index": 2, "disposition": {"attached_pic": 1}},
    ]
    _produced, commands = await _run_av("video/mp4", streams, frame_from_seek="any")
    assert commands[1][commands[1].index("-map") + 1] == "0:0"


@pytest.mark.asyncio
async def test_audio_uses_embedded_cover_without_seeking() -> None:
    produced, commands = await _run_av(
        "audio/mpeg", [{"index": 1, "disposition": {"attached_pic": 1}}], frame_from_seek="any"
    )
    assert produced is True
    ffmpeg = commands[1]
    assert "-ss" not in ffmpeg
    assert ffmpeg[ffmpeg.index("-map") + 1] == "0:1"


@pytest.mark.asyncio
async def test_audio_without_cover_has_no_thumbnail() -> None:
    produced, commands = await _run_av("audio/ogg", [])
    assert produced is False
    assert [c[0] for c in commands] == ["ffprobe"]


# ── Isolated parser operations (run in-process here) ──────────────────────────


def test_animated_gif_thumbnail_uses_first_frame() -> None:
    from app.core.security.parser_child import _render_thumbnail

    source = make_processing_temp_path(suffix=".gif")
    output = make_processing_temp_path(suffix=".webp")
    frames = [Image.new("RGB", (300, 200), color) for color in ("red", "blue", "green")]
    frames[0].save(source, save_all=True, append_images=frames[1:], duration=100, loop=0)
    try:
        result = _render_thumbnail([str(source), str(output), "640", "640", "85", "0"])
        assert result["blank"] is False
        with Image.open(output) as image:
            assert getattr(image, "n_frames", 1) == 1
            red, green, blue = image.convert("RGB").getpixel((5, 5))
            assert red > 200 and green < 60 and blue < 60
    finally:
        source.unlink(missing_ok=True)
        output.unlink(missing_ok=True)


def _write_epub(path: Path, opf: str, images: dict[str, Image.Image]) -> None:
    from io import BytesIO

    container = (
        '<?xml version="1.0"?><container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
        '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
        "</rootfiles></container>"
    )
    with ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", opf)
        for name, image in images.items():
            buffer = BytesIO()
            image.save(buffer, "PNG")
            archive.writestr(name, buffer.getvalue())


@pytest.mark.parametrize(
    "manifest",
    [
        # EPUB 3
        '<item id="c" href="img/cover%20art.png" media-type="image/png" properties="cover-image"/>',
        # EPUB 2
        '<item id="c" href="img/cover%20art.png" media-type="image/png"/>',
    ],
)
def test_epub_cover_extraction(manifest: str) -> None:
    from app.core.security.parser_child import _extract_epub_cover

    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
        '<metadata><meta name="cover" content="c"/></metadata><manifest>'
        f'{manifest}<item id="big" href="img/big.png" media-type="image/png"/>'
        "</manifest></package>"
    )
    source = make_processing_temp_path(suffix=".epub")
    output = make_processing_temp_path(suffix=".webp")
    _write_epub(
        source,
        opf,
        {
            "OEBPS/img/cover art.png": Image.new("RGB", (600, 900), "orange"),
            "OEBPS/img/big.png": Image.new("RGB", (2000, 2000), "white"),
        },
    )
    try:
        assert _extract_epub_cover([str(source), str(output), "640", "640", "85"]) == {
            "produced": True
        }
        with Image.open(output) as image:
            assert image.size == (427, 640)
            red, green, _blue = image.convert("RGB").getpixel((5, 5))
            assert red > 200 and 120 < green < 200
    finally:
        source.unlink(missing_ok=True)
        output.unlink(missing_ok=True)


def test_epub_without_declared_cover_produces_nothing() -> None:
    from app.core.security.parser_child import _extract_epub_cover

    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
        '<manifest><item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="fig" href="fig.png" media-type="image/png"/></manifest></package>'
    )
    source = make_processing_temp_path(suffix=".epub")
    output = make_processing_temp_path(suffix=".webp")
    _write_epub(source, opf, {"OEBPS/fig.png": Image.new("RGB", (10, 10), "red")})
    try:
        assert _extract_epub_cover([str(source), str(output), "640", "640", "85"]) == {
            "produced": False
        }
    finally:
        source.unlink(missing_ok=True)
        output.unlink(missing_ok=True)
