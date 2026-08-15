import base64
import json
import logging
import shutil
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

from PIL import Image

from app.core.events.processing import ProcessingFile
from app.core.observability.telemetry import get_tracer
from app.core.security.async_utils import shielded_to_thread as _shielded_to_thread
from app.core.security.file_security._concurrency import _get_concurrency_guard
from app.core.security.isolated_parser import (
    extract_office_thumbnail_isolated,
    render_thumbnail_isolated,
)
from app.core.security.processing_paths import processing_temp_dir
from app.core.security.sandbox import async_sandboxed_run

logger = logging.getLogger(__name__)


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
        generator_coro: Coroutine[Any, Any, bool | None]

        # Return None early for unsupported types — no exception, no retry needed.
        if mime_type == "image/svg+xml" or original_filename.lower().endswith(".svg"):
            generator_coro = _thumbnail_svg(pf.path, thumb_path, size, quality)
            check_blank = False
        elif mime_type.startswith("image/") or original_filename.lower().endswith(
            (
                ".jpg",
                ".jpeg",
                ".png",
                ".gif",
                ".webp",
                ".bmp",
                ".tiff",
                ".tif",
                ".ico",
                ".avif",
                ".jxl",
                ".heic",
            )
        ):
            generator_coro = _thumbnail_image(pf.path, thumb_path, size, quality)
            check_blank = True
        elif mime_type.startswith("video/") or original_filename.lower().endswith(
            (".mp4", ".webm", ".avi", ".mkv", ".mov", ".flv", ".wmv", ".m4v", ".ogv")
        ):
            generator_coro = _thumbnail_video(pf.path, thumb_path, size, quality)
            check_blank = False
        elif mime_type == "application/pdf" or original_filename.lower().endswith(".pdf"):
            generator_coro = _thumbnail_pdf(pf.path, thumb_path, size, quality)
            check_blank = False
        elif _is_office_mime(mime_type) or _is_office_filename(original_filename):
            ext = Path(original_filename).suffix.lower()
            if not ext or ext not in _OFFICE_EXTENSIONS:
                ext = _OFFICE_MIME_SUFFIXES.get(mime_type, ".docx")
            generator_coro = _thumbnail_office(pf.path, thumb_path, size, quality, suffix=ext)
            check_blank = False
        elif original_filename.lower().endswith(".ipynb"):
            generator_coro = _thumbnail_ipynb(pf.path, thumb_path, size, quality)
            check_blank = False
        elif mime_type in (
            "text/markdown",
            "text/x-markdown",
        ) or original_filename.lower().endswith((".md", ".markdown")):
            generator_coro = _thumbnail_via_soffice(
                pf.path, thumb_path, size, quality, suffix=".md"
            )
            check_blank = False
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
            check_blank = False
        else:
            logger.info("Skipping thumbnail for unsupported MIME type: %s", mime_type)
            return None

        try:
            rendered_blank = await generator_coro

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
            if check_blank and rendered_blank is True:
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


# ── Office MIME type helpers ─────────────────────────────────────────────────


def _is_blank_thumbnail(
    path: Path, brightness_threshold: float = 252.0, stddev_threshold: float = 4.0
) -> bool:
    """Measure generated/test images; production hostile inputs use the child result."""
    try:
        from PIL import ImageStat

        with Image.open(path) as image:
            gray = image.convert("L")
            try:
                stat = ImageStat.Stat(gray)
                return stat.mean[0] >= brightness_threshold and stat.stddev[0] <= stddev_threshold
            finally:
                gray.close()
    except Exception:
        return False


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
_OFFICE_EXTENSIONS = frozenset(
    {
        ".docx",
        ".xlsx",
        ".pptx",
        ".doc",
        ".xls",
        ".ppt",
        ".odt",
        ".ods",
        ".odp",
        ".rtf",
    }
)
_OFFICE_MIME_SUFFIXES: dict[str, str] = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/msword": ".doc",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "application/vnd.oasis.opendocument.presentation": ".odp",
    "application/rtf": ".rtf",
    "text/rtf": ".rtf",
}


def _is_office_filename(filename: str) -> bool:
    """Return True if the filename has an Office / OpenDocument extension."""
    return Path(filename).suffix.lower() in _OFFICE_EXTENSIONS


def _is_office_mime(mime_type: str) -> bool:
    """Return True for any Office / OpenDocument MIME type."""
    return any(sub in mime_type for sub in _OFFICE_SUBSTRINGS) or mime_type in _LEGACY_OFFICE_MIMES


def _soffice_command(*arguments: str) -> list[str]:
    """Build a LibreOffice command that works with the minimal sandbox procfs.

    In production containers ``/proc`` is intentionally an empty tmpfs so a
    hostile converter cannot inspect worker secrets via ``/proc/*/environ``.
    LibreOffice normally discovers its private shared libraries through
    ``/proc/self/exe``; provide the trusted installation directory explicitly
    instead of exposing the worker's full procfs.
    """
    executable = shutil.which("soffice")
    if executable is None:
        raise RuntimeError("LibreOffice (soffice) is required for document thumbnails")
    resolved = Path(executable).resolve()
    return [
        "/usr/bin/env",
        f"LD_LIBRARY_PATH={resolved.parent}",
        str(resolved),
        *arguments,
    ]


# ── Image helpers ─────────────────────────────────────────────────────────────


async def _thumbnail_image(
    input_path: Path, output_path: Path, size: tuple[int, int], quality: int
) -> bool:
    """Resize an image in the disposable parser process."""
    return await render_thumbnail_isolated(input_path, output_path, size=size, quality=quality)


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

        await render_thumbnail_isolated(
            temp_png,
            output_path,
            size=size,
            quality=quality,
            flatten_alpha=True,
        )


async def _thumbnail_video(
    input_path: Path, output_path: Path, size: tuple[int, int], quality: int
) -> None:
    """Extract a frame from video using FFmpeg."""
    # Heuristic: seek to 2 seconds or 10%
    # We use a simple 2s seek first as it's fastest
    with processing_temp_dir(prefix="video-thumb-") as temp_dir:
        # Use a lossless intermediary and preserve the source aspect ratio. The
        # old square JPEG path both distorted frames and applied two lossy
        # encodes before the browser ever saw the WebP.
        temp_frame = Path(temp_dir) / "frame.png"
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
                "-vf",
                (f"scale=w={size[0]}:h={size[1]}:force_original_aspect_ratio=decrease"),
                "-f",
                "image2",
                str(temp_frame),
            ]
            async with _get_concurrency_guard("subprocess"):
                process = await async_sandboxed_run(
                    cmd,
                    ro_paths=[input_path],
                    rw_paths=[temp_dir],
                    timeout=60,
                )
            if process.returncode == 0 and temp_frame.exists():
                await _thumbnail_image(temp_frame, output_path, size, quality)
                return
            temp_frame.unlink(missing_ok=True)

        raise RuntimeError(
            f"ffmpeg failed to generate a thumbnail for {input_path.name}: "
            f"{process.stderr.decode(errors='replace')[:300]}"
        )


def _get_pdf_page_dimension_inches(file_path: Path, page_index: int) -> tuple[float, float] | None:
    """Return (width_inches, height_inches) for a 0-indexed page in a PDF file, or None."""
    try:
        import pikepdf

        with pikepdf.open(str(file_path), suppress_warnings=True) as pdf:
            if page_index < 0 or page_index >= len(pdf.pages):
                return None
            page = pdf.pages[page_index]
            box = page.get("/CropBox") or page.get("/MediaBox")
            if box is None or len(box) != 4:
                return None
            user_unit = 1.0
            if "/UserUnit" in page:
                try:
                    user_unit = float(page["/UserUnit"])
                except (TypeError, ValueError):
                    user_unit = 1.0
            try:
                x0, y0, x1, y1 = [float(val) for val in box]
            except (TypeError, ValueError):
                return None
            width_pt = abs(x1 - x0) * user_unit
            height_pt = abs(y1 - y0) * user_unit
            if width_pt <= 0 or height_pt <= 0:
                return None
            return (width_pt / 72.0, height_pt / 72.0)
    except Exception:
        return None


def _compute_pdf_render_dpi(
    input_path: Path,
    page_num: int,
    size: tuple[int, int],
) -> int:
    """Compute the optimal rendering DPI dynamically based on the PDF page's physical dimensions.

    For standard A4/Letter pages, this produces ~80-150 DPI for crisp downsampling.
    For oversized pages (posters, digital whiteboards, large CAD canvases), this scales DPI down
    so intermediate renders never balloon into multi-million-pixel bitmaps or trigger
    Pillow DecompressionBombError.
    """
    target_dim_px = max(size[0], size[1]) * 1.5
    page_dim = _get_pdf_page_dimension_inches(input_path, page_num - 1)
    if page_dim is not None:
        width_in, height_in = page_dim
        max_dim_in = max(width_in, height_in)
        if max_dim_in > 0:
            computed_dpi = round(target_dim_px / max_dim_in)
            return max(12, min(300, computed_dpi))

    # Fallback if page dimensions could not be read (e.g. malformed or minimal test mock)
    # Assumes standard A4 width (~8.27 in)
    return max(96, min(200, round(size[0] * 1.5 / 8.27)))


async def _thumbnail_pdf(
    input_path: Path, output_path: Path, size: tuple[int, int], quality: int
) -> None:
    """Render the first non-blank page of a PDF using Ghostscript.

    Tries page 1 first. If the resulting thumbnail is nearly blank (common for
    attestation covers or title pages with minimal content), falls back to page 2.
    """
    with processing_temp_dir(prefix="pdf-thumb-") as temp_dir:
        for page_num in (1, 2):
            render_dpi = _compute_pdf_render_dpi(input_path, page_num, size)
            temp_png = Path(temp_dir) / f"page-{page_num}.png"
            cmd = [
                "gs",
                "-dSAFER",
                "-dBATCH",
                "-dNOPAUSE",
                "-sDEVICE=png16m",
                "-dTextAlphaBits=4",
                "-dGraphicsAlphaBits=4",
                f"-dFirstPage={page_num}",
                f"-dLastPage={page_num}",
                f"-r{render_dpi}",
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

            blank = await render_thumbnail_isolated(
                temp_png, output_path, size=size, quality=quality
            )

            if not output_path.exists():
                break

            if page_num == 1 and blank:
                logger.info(
                    "Page 1 of %s is blank — trying page 2 for a better thumbnail",
                    input_path.name,
                )
                # Keep page 1 in place until page 2 is successfully rendered.
                # Sparse single-page documents often cross the conservative
                # blank threshold; deleting their only usable preview made the
                # whole thumbnail stage fail when Ghostscript found no page 2.
                continue

            return


async def _thumbnail_office(
    input_path: Path,
    output_path: Path,
    size: tuple[int, int],
    quality: int,
    *,
    suffix: str = ".docx",
) -> None:
    """Render the first page of any Office document (OOXML, ODF, legacy OLE2).

    Strategy:
      1. Copy the source document to a temp directory with its correct extension
         so LibreOffice headless can reliably identify the format.
      2. Use LibreOffice headless to convert the document to PDF in that temp dir.
      3. Pass the resulting PDF through the existing Ghostscript → WebP pipeline.

    This works for every format LibreOffice supports: .docx, .xlsx, .pptx,
    .doc, .xls, .ppt, .odt, .ods, .odp — without relying on optional embedded
    thumbnails that most files simply do not contain.
    """
    with processing_temp_dir(prefix="lectern-office-thumb-") as tmp_dir:
        temp_file = tmp_dir / f"document{suffix}"
        await _shielded_to_thread(shutil.copy2, input_path, temp_file)

        # 1. Convert to PDF via LibreOffice headless, explicitly defining a custom
        # unique profile directory to avoid lock collisions between concurrent jobs.
        cmd = _soffice_command(
            f"-env:UserInstallation=file://{tmp_dir}",
            "--headless",
            "--norestore",
            "--nofirststartwizard",
            "--convert-to",
            "pdf",
            "--outdir",
            str(tmp_dir),
            str(temp_file),
        )

        async with _get_concurrency_guard("subprocess"):
            process = await async_sandboxed_run(
                cmd,
                ro_paths=[],
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

    try:
        produced = await extract_office_thumbnail_isolated(
            input_path, output_path, size=size, quality=quality
        )
        if not produced:
            output_path.unlink(missing_ok=True)
    except Exception as exc:
        output_path.unlink(missing_ok=True)
        logger.error("Fallback image processing failed for %s: %s", input_path.name, exc)


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

        cmd = _soffice_command(
            f"-env:UserInstallation=file://{tmp_dir}",
            "--headless",
            "--norestore",
            "--nofirststartwizard",
            "--convert-to",
            "pdf",
            "--outdir",
            str(tmp_dir),
            str(temp_file),
        )

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


def _ipynb_to_markdown(
    ipynb_bytes: bytes,
    img_dir: Path,
    max_cells: int = 20,
) -> tuple[str, Path | None]:
    """Extract leading notebook cells into formatted Markdown and raster images.

    Returns (markdown_text, first_extracted_image_path).
    """
    try:
        data = json.loads(ipynb_bytes.decode("utf-8", errors="replace"))
    except Exception:
        return "", None

    if not isinstance(data, dict):
        return "", None

    cells = data.get("cells")
    if not isinstance(cells, list):
        return "", None

    raw_metadata = data.get("metadata")
    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    raw_lang_info = metadata.get("language_info")
    lang_info = raw_lang_info if isinstance(raw_lang_info, dict) else {}
    raw_kernelspec = metadata.get("kernelspec")
    kernelspec = raw_kernelspec if isinstance(raw_kernelspec, dict) else {}
    lang = lang_info.get("name") or kernelspec.get("language") or "python"

    md_parts: list[str] = []
    first_image_path: Path | None = None
    img_counter = 0

    for cell in cells[:max_cells]:
        if not isinstance(cell, dict):
            continue
        cell_type = cell.get("cell_type")
        source_raw = cell.get("source")
        if isinstance(source_raw, list):
            source = "".join(str(s) for s in source_raw)
        elif isinstance(source_raw, str):
            source = source_raw
        else:
            source = ""

        if cell_type == "markdown":
            if source.strip():
                md_parts.append(source.strip())
            attachments = cell.get("attachments")
            if isinstance(attachments, dict):
                for _att_name, att_bundle in attachments.items():
                    if isinstance(att_bundle, dict):
                        for mime in ("image/png", "image/jpeg", "image/webp"):
                            b64 = att_bundle.get(mime)
                            if isinstance(b64, (str, list)):
                                b64_str = "".join(b64) if isinstance(b64, list) else b64
                                b64_clean = "".join(b64_str.split())
                                try:
                                    img_bytes = base64.b64decode(b64_clean, validate=True)
                                    if len(img_bytes) <= 20 * 1024 * 1024:
                                        img_counter += 1
                                        img_path = img_dir / f"att_{img_counter}.png"
                                        img_path.write_bytes(img_bytes)
                                        if first_image_path is None:
                                            first_image_path = img_path
                                        break
                                except Exception:
                                    pass
        elif cell_type == "code":
            if source.strip():
                md_parts.append(f"```{lang}\n{source.strip()}\n```")
            outputs = cell.get("outputs")
            if isinstance(outputs, list):
                for output in outputs:
                    if not isinstance(output, dict):
                        continue
                    out_type = output.get("output_type")
                    if out_type in ("display_data", "execute_result"):
                        out_data = output.get("data")
                        if isinstance(out_data, dict):
                            for mime in ("image/png", "image/jpeg", "image/webp"):
                                b64 = out_data.get(mime)
                                if isinstance(b64, (str, list)):
                                    b64_str = "".join(b64) if isinstance(b64, list) else b64
                                    b64_clean = "".join(b64_str.split())
                                    try:
                                        img_bytes = base64.b64decode(b64_clean, validate=True)
                                        if len(img_bytes) <= 20 * 1024 * 1024:
                                            img_counter += 1
                                            img_path = img_dir / f"output_{img_counter}.png"
                                            img_path.write_bytes(img_bytes)
                                            md_parts.append(f"![Output](output_{img_counter}.png)")
                                            if first_image_path is None:
                                                first_image_path = img_path
                                            break
                                    except Exception:
                                        pass
                    elif out_type == "stream":
                        text_raw = output.get("text")
                        if isinstance(text_raw, list):
                            stream_text = "".join(str(t) for t in text_raw)
                        elif isinstance(text_raw, str):
                            stream_text = text_raw
                        else:
                            stream_text = ""
                        if stream_text.strip():
                            first_line = stream_text.strip().splitlines()[0][:120]
                            md_parts.append(f"> Output: `{first_line}`")

    markdown_doc = "\n\n".join(md_parts).strip()
    return markdown_doc, first_image_path


async def _thumbnail_ipynb(
    input_path: Path, output_path: Path, size: tuple[int, int], quality: int
) -> None:
    """Render a thumbnail for a Jupyter notebook.

    1. Parses leading notebook cells into formatted Markdown with extracted output plots.
    2. Uses LibreOffice headless to convert the Markdown document to PDF.
    3. Renders the PDF first page to WebP via Ghostscript.
    4. Falls back to the first extracted plot image or raw text conversion if LibreOffice conversion fails.
    """
    with processing_temp_dir(prefix="lectern-ipynb-thumb-") as tmp_dir:
        raw_bytes = await _shielded_to_thread(input_path.read_bytes)
        md_content, first_image_path = _ipynb_to_markdown(raw_bytes, tmp_dir)

        if md_content:
            temp_md = tmp_dir / "document.md"
            temp_md.write_text(md_content, encoding="utf-8")

            cmd = _soffice_command(
                f"-env:UserInstallation=file://{tmp_dir}",
                "--headless",
                "--norestore",
                "--nofirststartwizard",
                "--convert-to",
                "pdf",
                "--outdir",
                str(tmp_dir),
                str(temp_md),
            )

            async with _get_concurrency_guard("subprocess"):
                process = await async_sandboxed_run(
                    cmd,
                    ro_paths=[],
                    rw_paths=[tmp_dir],
                    timeout=60,
                )

            pdf_files = list(tmp_dir.glob("*.pdf"))
            if pdf_files:
                await _thumbnail_pdf(pdf_files[0], output_path, size, quality)
                if output_path.exists():
                    return

        # Fallback 1: If LibreOffice produced no PDF, but we found an output plot, use it
        if first_image_path and first_image_path.exists():
            await _thumbnail_image(first_image_path, output_path, size, quality)
            if output_path.exists():
                return

        # Fallback 2: Plain text render via LibreOffice
        await _thumbnail_via_soffice(input_path, output_path, size, quality, suffix=".txt")
