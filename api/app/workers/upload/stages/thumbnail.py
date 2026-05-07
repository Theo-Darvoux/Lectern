import asyncio
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image

from app.core.processing import ProcessingFile
from app.core.telemetry import get_tracer

logger = logging.getLogger("wikint")

THUMBNAIL_SIZE = (640, 360)
THUMBNAIL_QUALITY = 85


async def run_thumbnail_stage(
    pf: ProcessingFile,
    mime_type: str,
    original_filename: str,
    tracer: Any = None,
    config: dict[str, Any] | None = None,
) -> str | None:
    """
    Generates a WebP thumbnail for the given processing file based on its MIME type.
    Returns the temporary path to the generated thumbnail file, or None if skipped.
    """
    if not tracer:
        tracer = get_tracer()

    with tracer.start_as_current_span("stage.thumbnail") as span:
        span.set_attribute("mime_type", mime_type)

        # Create a temp path for the thumbnail
        thumb_path = pf.path.parent / f"thumb_{pf.path.name}.webp"

        try:
            size_px = (
                config.get("thumbnail_size_px")
                if config and config.get("thumbnail_size_px") is not None
                else 640
            )
            quality = (
                config.get("thumbnail_quality")
                if config and config.get("thumbnail_quality") is not None
                else 85
            )
            size = (size_px, size_px)

            if mime_type.startswith("image/"):
                await _thumbnail_image(pf.path, thumb_path, size, quality)  # type: ignore[arg-type]
            elif mime_type.startswith("video/"):
                await _thumbnail_video(pf.path, thumb_path, size, quality)  # type: ignore[arg-type]
            elif mime_type == "application/pdf":
                await _thumbnail_pdf(pf.path, thumb_path, size, quality)  # type: ignore[arg-type]
            elif _is_office_mime(mime_type):
                await _thumbnail_office(pf.path, thumb_path, size, quality)  # type: ignore[arg-type]
            else:
                logger.info("Skipping thumbnail for unsupported MIME type: %s", mime_type)
                return None

            if thumb_path.exists():
                logger.info("Generated thumbnail for %s: %s", original_filename, thumb_path)
                return str(thumb_path)
        except Exception as e:
            logger.error("Failed to generate thumbnail for %s: %s", original_filename, e)
            if thumb_path.exists():
                thumb_path.unlink()

        return None


# ── Office MIME type helpers ─────────────────────────────────────────────────

# OOXML and ODF MIME types both contain one of these substrings.
_OFFICE_SUBSTRINGS = ("officedocument", "opendocument")
# Legacy OLE2 compound-file formats (binary .doc / .xls / .ppt).
_LEGACY_OFFICE_MIMES = frozenset(
    {
        "application/msword",
        "application/vnd.ms-excel",
        "application/vnd.ms-powerpoint",
    }
)


def _is_office_mime(mime_type: str) -> bool:
    """Return True for any Office / OpenDocument MIME type."""
    return any(sub in mime_type for sub in _OFFICE_SUBSTRINGS) or mime_type in _LEGACY_OFFICE_MIMES


# ── Image helpers ─────────────────────────────────────────────────────────────


async def _thumbnail_image(
    input_path: Path, output_path: Path, size: tuple[int, int], quality: int
) -> None:
    """Resize image to thumbnail using Pillow."""

    def _sync() -> None:
        with Image.open(input_path) as img:
            # Handle orientation if present
            if hasattr(img, "_getexif"):
                from PIL import ImageOps

                img = ImageOps.exif_transpose(img)  # type: ignore[assignment]

            img.thumbnail(size, Image.Resampling.LANCZOS)
            img.save(output_path, "WEBP", quality=quality)

    await asyncio.to_thread(_sync)


async def _thumbnail_video(
    input_path: Path, output_path: Path, size: tuple[int, int], quality: int
) -> None:
    """Extract a frame from video using FFmpeg."""
    # Heuristic: seek to 2 seconds or 10%
    # We use a simple 2s seek first as it's fastest
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        "00:00:02",
        "-i",
        str(input_path),
        "-vframes",
        "1",
        "-s",
        f"{size[0]}x{size[1]}",
        "-f",
        "image2",
        str(output_path.with_suffix(".jpg")),
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        # Fallback to 0s if 2s fails (e.g. very short video)
        cmd[3] = "00:00:00"
        process = await asyncio.create_subprocess_exec(*cmd)
        await process.wait()

    # Convert JPG to WebP for consistency
    temp_jpg = output_path.with_suffix(".jpg")
    if temp_jpg.exists():
        await _thumbnail_image(temp_jpg, output_path, size, quality)
        temp_jpg.unlink()


async def _thumbnail_pdf(
    input_path: Path, output_path: Path, size: tuple[int, int], quality: int
) -> None:
    """Render first page of PDF using Ghostscript."""
    # We render to a temporary PNG first then convert
    temp_png = output_path.with_suffix(".png")

    cmd = [
        "gs",
        "-dSAFER",
        "-dBATCH",
        "-dNOPAUSE",
        "-sDEVICE=png16m",
        "-dFirstPage=1",
        "-dLastPage=1",
        # Set resolution to match thumbnail size roughly (72dpi is standard, 150dpi for better quality)
        "-r150",
        f"-sOutputFile={temp_png}",
        str(input_path),
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    await process.communicate()

    if temp_png.exists():
        await _thumbnail_image(temp_png, output_path, size, quality)
        temp_png.unlink()


async def _thumbnail_office(
    input_path: Path, output_path: Path, size: tuple[int, int], quality: int
) -> None:
    """Render the first page of any Office document (OOXML, ODF, legacy OLE2).

    Strategy:
      1. Use LibreOffice headless to convert the document to PDF in a temp dir.
      2. Pass the resulting PDF through the existing Ghostscript → WebP pipeline.

    This works for every format LibreOffice supports: .docx, .xlsx, .pptx,
    .doc, .xls, .ppt, .odt, .ods, .odp — without relying on optional embedded
    thumbnails that most files simply do not contain.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="wikint_office_thumb_"))
    try:
        # 1. Convert to PDF via LibreOffice headless, explicitly defining a custom
        # unique profile directory to avoid lock collisions between concurrent jobs.
        cmd = [
            "soffice",
            f"-env:UserInstallation=file://{tmp_dir}",
            "--headless",
            "--norestore",
            "--nofirststartwizard",
            "--convert-to",
            "pdf",
            "--outdir",
            str(tmp_dir),
            str(input_path),
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=120)
        stdout_str = stdout_bytes.decode(errors="replace") if stdout_bytes else ""
        stderr_str = stderr_bytes.decode(errors="replace") if stderr_bytes else ""

        if process.returncode != 0:
            logger.error(
                "soffice conversion failed (rc=%d): %s",
                process.returncode,
                stderr_str,
            )
            # Fall back to grabbing the largest embedded image
            await _fallback_extract_largest_image(input_path, output_path, size, quality)
            return

        # 2. Find the produced PDF (LibreOffice names it after the source stem)
        pdf_files = list(tmp_dir.glob("*.pdf"))
        if not pdf_files:
            logger.error(
                "soffice produced no PDF for %s. out=%r, err=%r",
                input_path.name,
                stdout_str,
                stderr_str,
            )
            # Fall back to grabbing the largest embedded image
            await _fallback_extract_largest_image(input_path, output_path, size, quality)
            return

        pdf_path = pdf_files[0]

        # 3. Reuse the existing Ghostscript → Pillow → WebP pipeline
        await _thumbnail_pdf(pdf_path, output_path, size, quality)

    except TimeoutError:
        logger.error("soffice timed out converting %s", input_path.name)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def _fallback_extract_largest_image(
    input_path: Path, output_path: Path, size: tuple[int, int], quality: int
) -> None:
    """As a last resort for heavily complex or unrenderable OOXML/ODF files,
    open the raw zip container and extract the largest image.
    """

    def _extract() -> bytes | None:
        import zipfile

        try:
            with zipfile.ZipFile(input_path, "r") as z:
                # Filter for common image extensions
                image_entries = [
                    info
                    for info in z.infolist()
                    if info.filename.lower().endswith((".png", ".jpg", ".jpeg"))
                ]
                if not image_entries:
                    return None

                # Sort by size descending, grab the largest image
                image_entries.sort(key=lambda x: x.file_size, reverse=True)
                largest = image_entries[0]

                with z.open(largest) as f:
                    return f.read()
        except zipfile.BadZipFile:
            return None
        except Exception as e:
            logger.error("Fallback image extraction failed for %s: %s", input_path.name, e)
            return None

    data = await asyncio.to_thread(_extract)
    if not data:
        return

    # Process extracted bytes with Pillow
    def _sync_process(img_data: bytes) -> None:
        import io

        try:
            with Image.open(io.BytesIO(img_data)) as img:
                img.thumbnail(size, Image.Resampling.LANCZOS)
                img.save(output_path, "WEBP", quality=quality)
        except Exception as e:
            logger.error("Fallback image processing failed: %s", e)

    await asyncio.to_thread(_sync_process, data)
