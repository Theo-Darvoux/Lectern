"""Production-container smoke for the hostile parser boundary."""

from __future__ import annotations

import asyncio
import shutil
import zipfile
from pathlib import Path

from PIL import Image

from app.core.security.isolated_parser import (
    extract_zip_isolated,
    inspect_upload,
    process_avatar_isolated,
    render_thumbnail_isolated,
)
from app.core.security.sandbox import async_sandboxed_run


def _write_minimal_xlsx(path: Path) -> None:
    """Create a dependency-free workbook for the production converter smoke."""
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
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as workbook:
        for name, content in members.items():
            workbook.writestr(name, content)


async def main() -> None:
    root = Path("/var/lib/lectern/processing")
    source = root / "smoke.txt"
    source.write_text("safe", encoding="utf-8")
    image = root / "avatar.png"
    Image.new("RGBA", (2, 2), (0, 0, 255, 128)).save(image)
    archive = root / "batch.zip"
    extraction = root / "extracted"
    extraction.mkdir()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("file.txt", b"batch")

    inspection = await inspect_upload(
        source,
        filename="smoke.txt",
        declared_mime="text/plain",
        inspect_archive=False,
    )
    assert inspection.actual_mime == "text/plain"
    assert (await process_avatar_isolated(image)).startswith(b"RIFF")

    thumbnail = root / "thumbnail.webp"
    await render_thumbnail_isolated(
        image,
        thumbnail,
        size=(4, 4),
        quality=80,
        flatten_alpha=True,
    )
    assert thumbnail.read_bytes().startswith(b"RIFF")

    entries, _skipped = await extract_zip_isolated(
        archive,
        extraction_root=extraction,
        max_members=2,
    )
    assert len(entries) == 1
    assert entries[0].tmp_path.read_bytes() == b"batch"

    # Only the worker image includes LibreOffice. This exercises the complete
    # Office -> PDF -> WebP path under the exact production Bubblewrap profile;
    # a host-only test cannot catch container-specific /proc mount behavior.
    if shutil.which("soffice"):
        from app.core.events.processing import ProcessingFile
        from app.workers.upload.stages.thumbnail import run_thumbnail_stage

        # Upload workers deliberately use extensionless processing paths; keep
        # that production detail in the smoke while supplying the real filename
        # separately to the routing layer.
        workbook = root / "office-input"
        _write_minimal_xlsx(workbook)
        processing_file = ProcessingFile(workbook, workbook.stat().st_size)
        office_thumbnail = await run_thumbnail_stage(
            processing_file,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "courses.xlsx",
        )
        assert office_thumbnail is not None
        with Image.open(office_thumbnail) as generated:
            assert generated.format == "WEBP"
            assert generated.width <= 640 and generated.height <= 640
        Path(office_thumbnail).unlink()

        markdown = root / "notes.md"
        markdown.write_text("# Course notes\n\nA visible document preview.\n", encoding="utf-8")
        markdown_file = ProcessingFile(markdown, markdown.stat().st_size)
        text_thumbnail = await run_thumbnail_stage(
            markdown_file,
            "text/markdown",
            "notes.md",
        )
        assert text_thumbnail is not None
        with Image.open(text_thumbnail) as generated:
            assert generated.format == "WEBP"
        Path(text_thumbnail).unlink()

        # The other external thumbnail engines do not use LibreOffice, but run
        # them under the same container sandbox so a future mount-policy change
        # cannot silently break another supported family.
        converter_smokes = (
            ["pdftoppm", "-v"],
            ["gs", "--version"],
            ["ffmpeg", "-version"],
            ["rsvg-convert", "--version"],
        )
        for command in converter_smokes:
            completed = await async_sandboxed_run(command, timeout=10)
            assert completed.returncode == 0, completed.stderr.decode(errors="replace")

    print("container-parser-ok")


if __name__ == "__main__":
    asyncio.run(main())
