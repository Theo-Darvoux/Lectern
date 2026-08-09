"""Production-container smoke for the hostile parser boundary."""

from __future__ import annotations

import asyncio
import zipfile
from pathlib import Path

from PIL import Image

from app.core.security.isolated_parser import (
    extract_zip_isolated,
    inspect_upload,
    process_avatar_isolated,
    render_thumbnail_isolated,
)


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
    print("container-parser-ok")


if __name__ == "__main__":
    asyncio.run(main())
