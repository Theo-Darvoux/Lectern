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
            elif mime_type in (
                "text/markdown",
                "text/x-markdown",
            ) or original_filename.lower().endswith((".md", ".markdown")):
                await _thumbnail_markdown(pf.path, thumb_path, size, quality)  # type: ignore[arg-type]
            elif mime_type == "application/vnd.wikint.qcm+json" or original_filename.lower().endswith(".qcm"):
                await _thumbnail_qcm(pf.path, thumb_path, size, quality)  # type: ignore[arg-type]
            elif mime_type.startswith("text/") or original_filename.lower().endswith(
                (
                    ".txt",
                    ".tex",
                    ".py",
                    ".js",
                    ".ts",
                    ".json",
                    ".html",
                    ".css",
                    ".sh",
                    ".yaml",
                    ".yml",
                    ".ini",
                    ".conf",
                    ".sql",
                )
            ):
                await _thumbnail_text(pf.path, thumb_path, size, quality)  # type: ignore[arg-type]
            else:
                logger.info("Skipping thumbnail for unsupported MIME type: %s", mime_type)
                return None

            if thumb_path.exists():
                # Only discard near-blank thumbnails for raster images, where an
                # all-white result genuinely means there is nothing to show. For
                # PDF/Office/video the dedicated pipelines already select the best
                # available page/frame (and fall back across pages), so we keep
                # their output as best effort — otherwise single-page or pale
                # documents (e.g. featured PDFs) end up with no thumbnail at all.
                if mime_type.startswith("image/") and _is_blank_thumbnail(thumb_path):
                    logger.info(
                        "Thumbnail for %s is nearly blank — discarding to allow native fallback",
                        original_filename,
                    )
                    thumb_path.unlink()
                    return None
                logger.info("Generated thumbnail for %s: %s", original_filename, thumb_path)
                return str(thumb_path)
        except Exception as e:
            logger.error("Failed to generate thumbnail for %s: %s", original_filename, e)
            if thumb_path.exists():
                thumb_path.unlink()

        return None


def _is_blank_thumbnail(
    path: Path, brightness_threshold: float = 252.0, stddev_threshold: float = 4.0
) -> bool:
    """Return True if the thumbnail is nearly all white (blank document page).

    A thumbnail is considered blank when both:
    - mean grayscale brightness ≥ brightness_threshold (very bright)
    - pixel stddev ≤ stddev_threshold (very little contrast)

    Thresholds are deliberately strict so only an essentially uniform white image
    is treated as blank. Using both guards prevents discarding legitimately bright
    content like snow photos, pale-background slides, or PDF title pages that
    still have a small amount of visible text.
    """
    try:
        from PIL import ImageStat

        with Image.open(path) as img:
            gray = img.convert("L")
            stat = ImageStat.Stat(gray)
            mean = stat.mean[0]
            stddev = stat.stddev[0]
            return mean >= brightness_threshold and stddev <= stddev_threshold
    except Exception:
        return False


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
        try:
            await _thumbnail_image(temp_jpg, output_path, size, quality)
        finally:
            temp_jpg.unlink(missing_ok=True)


async def _thumbnail_pdf(
    input_path: Path, output_path: Path, size: tuple[int, int], quality: int
) -> None:
    """Render the first non-blank page of a PDF using Ghostscript.

    Tries page 1 first. If the resulting thumbnail is nearly blank (common for
    attestation covers or title pages with minimal content), falls back to page 2.
    """
    for page_num in (1, 2):
        temp_png = output_path.with_suffix(f".p{page_num}.png")
        cmd = [
            "gs",
            "-dSAFER",
            "-dBATCH",
            "-dNOPAUSE",
            "-sDEVICE=png16m",
            f"-dFirstPage={page_num}",
            f"-dLastPage={page_num}",
            "-r150",
            f"-sOutputFile={temp_png}",
            str(input_path),
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, stderr_bytes = await process.communicate()

        if not temp_png.exists():
            logger.warning(
                "Ghostscript produced no output for page %d of %s (rc=%d): %s",
                page_num,
                input_path.name,
                process.returncode,
                stderr_bytes.decode(errors="replace")[:300],
            )
            break  # If Ghostscript fails, subsequent pages are unlikely to succeed either

        try:
            await _thumbnail_image(temp_png, output_path, size, quality)
        finally:
            temp_png.unlink(missing_ok=True)

        if not output_path.exists():
            break

        # Page 1 blank → try page 2 for a more representative thumbnail.
        if page_num == 1 and _is_blank_thumbnail(output_path):
            logger.info(
                "Page 1 of %s is blank — trying page 2 for a better thumbnail",
                input_path.name,
            )
            output_path.unlink(missing_ok=True)
            continue

        return  # success


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


async def _thumbnail_markdown(
    input_path: Path, output_path: Path, size: tuple[int, int], quality: int
) -> None:
    """Render a Markdown file by copying it to a temp .md file and converting to PDF/WebP."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="wikint_md_thumb_"))
    try:
        # Copy file to have a .md suffix so LibreOffice recognizes it as markdown
        temp_md = tmp_dir / "document.md"
        shutil.copy2(input_path, temp_md)

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
            str(temp_md),
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=60)

        pdf_files = list(tmp_dir.glob("*.pdf"))
        if not pdf_files:
            logger.error(
                "soffice produced no PDF for markdown. out=%r, err=%r",
                stdout_bytes.decode(errors="replace") if stdout_bytes else "",
                stderr_bytes.decode(errors="replace") if stderr_bytes else "",
            )
            return

        pdf_path = pdf_files[0]
        await _thumbnail_pdf(pdf_path, output_path, size, quality)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def _thumbnail_text(
    input_path: Path, output_path: Path, size: tuple[int, int], quality: int
) -> None:
    """Render a text/code file by copying it to a temp .txt file and converting to PDF/WebP."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="wikint_txt_thumb_"))
    try:
        # Copy file to have a .txt suffix so LibreOffice imports it cleanly as plain text
        temp_txt = tmp_dir / "document.txt"
        shutil.copy2(input_path, temp_txt)

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
            str(temp_txt),
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=60)

        pdf_files = list(tmp_dir.glob("*.pdf"))
        if not pdf_files:
            logger.error(
                "soffice produced no PDF for text file. out=%r, err=%r",
                stdout_bytes.decode(errors="replace") if stdout_bytes else "",
                stderr_bytes.decode(errors="replace") if stderr_bytes else "",
            )
            return

        pdf_path = pdf_files[0]
        await _thumbnail_pdf(pdf_path, output_path, size, quality)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def _thumbnail_qcm(
    input_path: Path, output_path: Path, size: tuple[int, int], quality: int
) -> None:
    """Render a visual card for a QCM file."""

    def _sync() -> None:
        import json
        from PIL import ImageDraw, ImageFont, Image

        try:
            with open(input_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.error("Failed to parse QCM file for thumbnail: %s", e)
            return

        chapters = data.get("chapters", [])
        title = "QCM"
        if chapters and chapters[0].get("title"):
            title = chapters[0]["title"]

        questions = []
        for ch in chapters:
            for q in ch.get("questions", []):
                questions.append(q)

        # Create image with deep purple background (violet-700)
        img = Image.new("RGB", size, "#6d28d9")
        draw = ImageDraw.Draw(img)

        try:
            font_path_bold = "/usr/share/fonts/liberation-sans-fonts/LiberationSans-Bold.ttf"
            font_path_regular = "/usr/share/fonts/liberation-sans-fonts/LiberationSans-Regular.ttf"
            font_title = ImageFont.truetype(font_path_bold, 42)
            font_header = ImageFont.truetype(font_path_bold, 20)
            font_text = ImageFont.truetype(font_path_regular, 20)
            font_qcm = ImageFont.truetype(font_path_bold, 120)
        except OSError:
            font_title = ImageFont.load_default()
            font_header = ImageFont.load_default()
            font_text = ImageFont.load_default()
            font_qcm = ImageFont.load_default()

        center_x = size[0] // 2

        # Draw huge "QCM" watermark in darker purple (violet-800)
        try:
            draw.text((center_x, size[1] // 2), "QCM", fill="#5b21b6", font=font_qcm, anchor="mm")
        except TypeError:
            # Fallback if anchor is not supported in older Pillow
            draw.text((center_x - 100, size[1] // 2 - 60), "QCM", fill="#5b21b6", font=font_qcm)

        # Draw header text (violet-200)
        header_text = "QUESTIONNAIRE À CHOIX MULTIPLES"
        try:
            draw.text((center_x, 60), header_text, fill="#ddd6fe", font=font_header, anchor="mt")
        except TypeError:
            draw.text((80, 60), header_text, fill="#ddd6fe", font=font_header)

        # Draw title (white)
        title_truncated = title[:40] + "..." if len(title) > 40 else title
        try:
            draw.text((center_x, size[1] // 2), title_truncated, fill="#ffffff", font=font_title, anchor="mm")
        except TypeError:
            draw.text((80, size[1] // 2), title_truncated, fill="#ffffff", font=font_title)

        # Draw questions count (violet-200)
        q_count = len(questions)
        q_text = f"{q_count} question{'s' if q_count != 1 else ''}"
        try:
            draw.text((center_x, size[1] - 80), q_text, fill="#ddd6fe", font=font_text, anchor="mb")
        except TypeError:
            draw.text((center_x - 40, size[1] - 80), q_text, fill="#ddd6fe", font=font_text)

        img.save(output_path, "WEBP", quality=quality)

    await asyncio.to_thread(_sync)
