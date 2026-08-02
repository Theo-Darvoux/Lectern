import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any

from PIL import Image

from app.core.events.processing import ProcessingFile
from app.core.observability.telemetry import get_tracer
from app.core.security.async_utils import shielded_to_thread as _shielded_to_thread
from app.core.security.file_security._concurrency import _get_concurrency_guard, image_guard
from app.core.security.file_security._image import _validate_image_size
from app.core.security.processing_paths import processing_temp_dir
from app.core.security.sandbox import async_sandboxed_run

logger = logging.getLogger(__name__)

_THUMBNAIL_EMBEDDED_IMAGE_MAX_BYTES = 20 * 1024 * 1024


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

        _size_cfg = (config or {}).get("thumbnail_size_px")
        size_px = int(_size_cfg) if _size_cfg is not None else 640
        _qual_cfg = (config or {}).get("thumbnail_quality")
        quality = int(_qual_cfg) if _qual_cfg is not None else 85
        size = (size_px, size_px)

        # Return None early for unsupported types — no exception, no retry needed.
        if mime_type == "image/svg+xml":
            generator_coro = _thumbnail_svg(pf.path, thumb_path, size, quality)
        elif mime_type.startswith("image/"):
            generator_coro = _thumbnail_image(pf.path, thumb_path, size, quality)
        elif mime_type.startswith("video/"):
            generator_coro = _thumbnail_video(pf.path, thumb_path, size, quality)
        elif mime_type == "application/pdf":
            generator_coro = _thumbnail_pdf(pf.path, thumb_path, size, quality)
        elif _is_office_mime(mime_type):
            generator_coro = _thumbnail_office(pf.path, thumb_path, size, quality)
        elif mime_type in (
            "text/markdown",
            "text/x-markdown",
        ) or original_filename.lower().endswith((".md", ".markdown")):
            generator_coro = _thumbnail_via_soffice(
                pf.path, thumb_path, size, quality, suffix=".md"
            )
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
            generator_coro = _thumbnail_via_soffice(
                pf.path, thumb_path, size, quality, suffix=".txt"
            )
        else:
            logger.info("Skipping thumbnail for unsupported MIME type: %s", mime_type)
            return None

        try:
            await generator_coro

            if not thumb_path.exists():
                raise RuntimeError(
                    f"Thumbnail generator produced no output for {original_filename!r}"
                )

            # Only discard near-blank thumbnails for raster images, where an
            # all-white result genuinely means there is nothing to show. For
            # PDF/Office/video the dedicated pipelines already select the best
            # available page/frame (and fall back across pages), so we keep
            # their output as best effort — otherwise single-page or pale
            # documents (e.g. featured PDFs) end up with no thumbnail at all.
            # SVGs are excluded: vector art often has transparent/white fills
            # that are legitimate and should not be discarded.
            if (
                mime_type.startswith("image/")
                and mime_type != "image/svg+xml"
                and _is_blank_thumbnail(thumb_path)
            ):
                logger.info(
                    "Thumbnail for %s is nearly blank — discarding to allow native fallback",
                    original_filename,
                )
                thumb_path.unlink()
                return None

            logger.info("Generated thumbnail for %s: %s", original_filename, thumb_path)
            return str(thumb_path)
        except Exception:
            if thumb_path.exists():
                thumb_path.unlink()
            raise


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
            try:
                stat = ImageStat.Stat(gray)
                mean = stat.mean[0]
                stddev = stat.stddev[0]
                return mean >= brightness_threshold and stddev <= stddev_threshold
            finally:
                gray.close()
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
        with Image.open(input_path) as base_img:
            _validate_image_size(base_img)
            if getattr(base_img, "n_frames", 1) != 1 or getattr(
                base_img, "is_animated", False
            ):
                raise ValueError("Animated thumbnail sources are not supported")
            base_img.load()

            from PIL import ImageOps

            oriented = ImageOps.exif_transpose(base_img)
            try:
                oriented.thumbnail(size, Image.Resampling.LANCZOS)
                oriented.save(output_path, "WEBP", quality=quality)
            finally:
                if oriented is not base_img:
                    oriented.close()

    async with image_guard():
        await _shielded_to_thread(_sync)


async def _thumbnail_svg(
    input_path: Path, output_path: Path, size: tuple[int, int], quality: int
) -> None:
    """Render SVG to a WebP thumbnail via rsvg-convert → Pillow.

    rsvg-convert produces RGBA PNGs for SVGs without an explicit background fill.
    We composite against white so the WebP thumbnail always has a solid background
    and renders correctly in the UI (no checkerboard transparency artefact).
    """
    # Bind a private directory rather than a pre-created output file.  librsvg
    # writes by atomically replacing its output; a file bind would leave the
    # replacement inside the sandbox mount namespace and the host would still
    # see the original empty inode.
    with processing_temp_dir(prefix="svg-thumb-") as temp_dir:
        temp_png = temp_dir / "render.png"
        cmd = [
            "rsvg-convert",
            "--width",
            str(size[0]),
            "--height",
            str(size[1]),
            "--keep-aspect-ratio",
            "--output",
            str(temp_png),
            str(input_path),
        ]
        async with _get_concurrency_guard("subprocess"):
            process = await async_sandboxed_run(
                cmd,
                ro_paths=[input_path],
                rw_paths=[temp_dir],
                timeout=60,
            )
        if process.returncode != 0 or not temp_png.exists():
            raise RuntimeError(
                f"rsvg-convert failed for {input_path.name}: "
                f"{process.stderr.decode(errors='replace')[:300]}"
            )

        def _flatten_and_save() -> None:
            with Image.open(temp_png) as img:
                _validate_image_size(img)
                img.thumbnail(size, Image.Resampling.LANCZOS)
                if img.mode in ("RGBA", "LA", "PA"):
                    rgba_img = img.convert("RGBA") if img.mode != "RGBA" else img
                    try:
                        alpha = rgba_img.getchannel("A")
                        try:
                            background = Image.new("RGB", rgba_img.size, "white")
                            try:
                                background.paste(rgba_img, mask=alpha)
                                background.save(output_path, "WEBP", quality=quality)
                            finally:
                                background.close()
                        finally:
                            alpha.close()
                    finally:
                        if rgba_img is not img:
                            rgba_img.close()
                else:
                    rgb = img.convert("RGB")
                    try:
                        rgb.save(output_path, "WEBP", quality=quality)
                    finally:
                        rgb.close()

        async with image_guard():
            await _shielded_to_thread(_flatten_and_save)


async def _thumbnail_video(
    input_path: Path, output_path: Path, size: tuple[int, int], quality: int
) -> None:
    """Extract a frame from video using FFmpeg."""
    # Heuristic: seek to 2 seconds or 10%
    # We use a simple 2s seek first as it's fastest
    with processing_temp_dir(prefix="video-thumb-") as temp_dir:
        temp_jpg = Path(temp_dir) / "frame.jpg"
        for seek in ("00:00:02", "00:00:00"):
            cmd = [
                "ffmpeg",
                "-y",
                "-ss",
                seek,
                "-i",
                str(input_path),
                "-vframes",
                "1",
                "-s",
                f"{size[0]}x{size[1]}",
                "-f",
                "image2",
                str(temp_jpg),
            ]
            async with _get_concurrency_guard("subprocess"):
                process = await async_sandboxed_run(
                    cmd,
                    ro_paths=[input_path],
                    rw_paths=[temp_dir],
                    timeout=60,
                )
            if process.returncode == 0 and temp_jpg.exists():
                await _thumbnail_image(temp_jpg, output_path, size, quality)
                return
            temp_jpg.unlink(missing_ok=True)

        raise RuntimeError(
            f"ffmpeg failed to generate a thumbnail for {input_path.name}: "
            f"{process.stderr.decode(errors='replace')[:300]}"
        )


async def _thumbnail_pdf(
    input_path: Path, output_path: Path, size: tuple[int, int], quality: int
) -> None:
    """Render the first non-blank page of a PDF using Ghostscript.

    Tries page 1 first. If the resulting thumbnail is nearly blank (common for
    attestation covers or title pages with minimal content), falls back to page 2.
    """
    with processing_temp_dir(prefix="pdf-thumb-") as temp_dir:
        for page_num in (1, 2):
            temp_png = Path(temp_dir) / f"page-{page_num}.png"
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

            async with _get_concurrency_guard("subprocess"):
                process = await async_sandboxed_run(
                    cmd,
                    ro_paths=[input_path],
                    rw_paths=[temp_dir],
                    timeout=60,
                )

            if process.returncode != 0 or not temp_png.exists():
                logger.warning(
                    "Ghostscript produced no output for page %d of %s (rc=%d): %s",
                    page_num,
                    input_path.name,
                    process.returncode,
                    process.stderr.decode(errors="replace")[:300],
                )
                break

            await _thumbnail_image(temp_png, output_path, size, quality)

            if not output_path.exists():
                break

            if page_num == 1 and _is_blank_thumbnail(output_path):
                logger.info(
                    "Page 1 of %s is blank — trying page 2 for a better thumbnail",
                    input_path.name,
                )
                output_path.unlink(missing_ok=True)
                continue

            return


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
    with processing_temp_dir(prefix="lectern-office-thumb-") as tmp_dir:
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

        async with _get_concurrency_guard("subprocess"):
            process = await async_sandboxed_run(
                cmd,
                ro_paths=[input_path],
                rw_paths=[tmp_dir],
                timeout=120,
            )
        stdout_str = process.stdout.decode(errors="replace") if process.stdout else ""
        stderr_str = process.stderr.decode(errors="replace") if process.stderr else ""

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
                if largest.file_size > _THUMBNAIL_EMBEDDED_IMAGE_MAX_BYTES:
                    raise ValueError("Embedded thumbnail candidate exceeds byte limit")

                with z.open(largest) as f:
                    data = f.read(_THUMBNAIL_EMBEDDED_IMAGE_MAX_BYTES + 1)
                    if len(data) > _THUMBNAIL_EMBEDDED_IMAGE_MAX_BYTES:
                        raise ValueError("Embedded thumbnail candidate expanded beyond byte limit")
                    return data
        except zipfile.BadZipFile:
            return None
        except Exception as e:
            logger.error("Fallback image extraction failed for %s: %s", input_path.name, e)
            return None

    data = await _shielded_to_thread(_extract)
    if not data:
        return

    # Process extracted bytes with Pillow
    def _sync_process(img_data: bytes) -> None:
        import io

        try:
            with io.BytesIO(img_data) as source, Image.open(source) as img:
                _validate_image_size(img)
                if getattr(img, "n_frames", 1) != 1 or getattr(
                    img, "is_animated", False
                ):
                    raise ValueError("Animated embedded thumbnails are not supported")
                img.load()
                img.thumbnail(size, Image.Resampling.LANCZOS)
                img.save(output_path, "WEBP", quality=quality)
        except Exception as e:
            logger.error("Fallback image processing failed: %s", e)

    async with image_guard():
        await _shielded_to_thread(_sync_process, data)


async def _thumbnail_via_soffice(
    input_path: Path,
    output_path: Path,
    size: tuple[int, int],
    quality: int,
    *,
    suffix: str,
) -> None:
    """Convert a plain-text or Markdown file to a thumbnail via LibreOffice → Ghostscript.

    LibreOffice requires the source file to have the right extension to identify the
    format.  ``suffix`` is appended to the temp copy (e.g. ``.md`` or ``.txt``).
    """
    with processing_temp_dir(prefix="lectern-soffice-thumb-") as tmp_dir:
        temp_file = tmp_dir / f"document{suffix}"
        await _shielded_to_thread(shutil.copy2, input_path, temp_file)

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
            str(temp_file),
        ]

        async with _get_concurrency_guard("subprocess"):
            process = await async_sandboxed_run(
                cmd,
                ro_paths=[],
                rw_paths=[tmp_dir],
                timeout=60,
            )
        stdout_bytes, stderr_bytes = process.stdout, process.stderr

        pdf_files = list(tmp_dir.glob("*.pdf"))
        if not pdf_files:
            logger.error(
                "soffice produced no PDF for %s file. out=%r, err=%r",
                suffix,
                stdout_bytes.decode(errors="replace") if stdout_bytes else "",
                stderr_bytes.decode(errors="replace") if stderr_bytes else "",
            )
            return

        await _thumbnail_pdf(pdf_files[0], output_path, size, quality)
