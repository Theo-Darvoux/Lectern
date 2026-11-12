"""Render a WebP thumbnail for a sanitized file.

Every external renderer (pdftoppm, LibreOffice, ffmpeg, rsvg-convert, ddjvu) runs
in the sandbox, and every raster decode runs in the disposable parser process.
Each renderer returns True when it wrote ``output_path`` and False when the file
has nothing to show (an audio file without cover art, an EPUB without a declared
cover). Deterministic render failures raise ``ThumbnailRenderError``.
"""

import json
import logging
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.core.events.processing import ProcessingFile
from app.core.media.thumbnails import (
    OFFICE_MIME_SUFFIXES,
    ThumbnailKind,
    effective_mime,
    thumbnail_kind,
)
from app.core.observability.telemetry import get_tracer
from app.core.security.async_utils import shielded_to_thread as _shielded_to_thread
from app.core.security.file_security._concurrency import _get_concurrency_guard
from app.core.security.isolated_parser import (
    extract_epub_cover_isolated,
    extract_office_thumbnail_isolated,
    render_thumbnail_isolated,
)
from app.core.security.processing_paths import processing_temp_dir
from app.core.security.sandbox import async_sandboxed_run

logger = logging.getLogger(__name__)

_DEFAULT_SIZE_PX = 640
_DEFAULT_QUALITY = 85
# Render rasters 1.5x larger than the target so the final LANCZOS downscale stays crisp.
_SUPERSAMPLE = 1.5


class ThumbnailRenderError(ValueError):
    """The file could not be rendered. Retrying the same bytes will not help."""


Renderer = Callable[[Path, Path, tuple[int, int], int, str], Awaitable[bool]]


async def run_thumbnail_stage(
    pf: ProcessingFile,
    mime_type: str,
    original_filename: str,
    tracer: Any = None,
    config: dict[str, Any] | None = None,
) -> str | None:
    """Render a WebP thumbnail next to ``pf.path``.

    Returns the thumbnail path, or None when the file type never gets a
    thumbnail or the file has nothing to show.
    """
    tracer = tracer or get_tracer()
    with tracer.start_as_current_span("stage.thumbnail") as span:
        span.set_attribute("mime_type", mime_type)
        kind = thumbnail_kind(mime_type, original_filename)
        if kind is None:
            logger.info("No thumbnail for MIME type %s", mime_type)
            return None
        span.set_attribute("thumbnail.kind", kind.value)

        cfg = config or {}
        size_cfg, quality_cfg = cfg.get("thumbnail_size_px"), cfg.get("thumbnail_quality")
        size_px = int(size_cfg) if size_cfg is not None else _DEFAULT_SIZE_PX
        quality = int(quality_cfg) if quality_cfg is not None else _DEFAULT_QUALITY
        # The source path is worker-controlled, never the user's filename.
        thumb_path = pf.path.with_name(f"{pf.path.name}.thumb.webp")
        mime = effective_mime(mime_type, original_filename)

        try:
            produced = await _renderer(kind)(pf.path, thumb_path, (size_px, size_px), quality, mime)
            if not produced:
                thumb_path.unlink(missing_ok=True)
                logger.info("No thumbnail content in %s (%s)", original_filename, kind.value)
                return None
            if not thumb_path.is_file() or thumb_path.stat().st_size == 0:
                raise ThumbnailRenderError(f"{kind.value} renderer produced no output")
        except Exception:
            thumb_path.unlink(missing_ok=True)
            raise

        logger.info("Generated %s thumbnail for %s", kind.value, original_filename)
        return str(thumb_path)


async def _run_tool(
    cmd: list[str], *, ro: list[Path], rw: list[Path], timeout: int = 60
) -> tuple[bool, str]:
    """Run one sandboxed renderer. Return (succeeded, stderr excerpt)."""
    async with _get_concurrency_guard("subprocess"):
        process = await async_sandboxed_run(cmd, ro_paths=ro, rw_paths=rw, timeout=timeout)
    stderr = (process.stderr or b"").decode(errors="replace")[:300]
    return process.returncode == 0, stderr


# ── Images ───────────────────────────────────────────────────────────────────


async def _thumbnail_image(
    input_path: Path, output_path: Path, size: tuple[int, int], quality: int, _mime: str = ""
) -> bool:
    await render_thumbnail_isolated(input_path, output_path, size=size, quality=quality)
    return True


async def _thumbnail_svg(
    input_path: Path, output_path: Path, size: tuple[int, int], quality: int, _mime: str
) -> bool:
    """Rasterize with rsvg-convert, then flatten onto white.

    rsvg-convert writes by atomically replacing its output, so it gets a private
    directory: a bind-mounted file would keep the empty inode on the host side.
    """
    with processing_temp_dir(prefix="svg-thumb-") as temp_dir:
        temp_png = temp_dir / "render.png"
        ok, stderr = await _run_tool(
            [
                "rsvg-convert",
                "--width",
                str(size[0]),
                "--height",
                str(size[1]),
                "--keep-aspect-ratio",
                "--output",
                str(temp_png),
                str(input_path),
            ],
            ro=[input_path],
            rw=[temp_dir],
        )
        if not ok or not temp_png.exists():
            raise ThumbnailRenderError(f"rsvg-convert failed: {stderr}")
        await render_thumbnail_isolated(
            temp_png, output_path, size=size, quality=quality, flatten_alpha=True
        )
    return True


async def _thumbnail_djvu(
    input_path: Path, output_path: Path, size: tuple[int, int], quality: int, _mime: str
) -> bool:
    """Render page 1 with ddjvu, which keeps the aspect ratio inside ``-size``."""
    target = round(max(size) * _SUPERSAMPLE)
    with processing_temp_dir(prefix="djvu-thumb-") as temp_dir:
        page = temp_dir / "page.pnm"
        ok, stderr = await _run_tool(
            [
                "ddjvu",
                "-format=pnm",
                "-page=1",
                f"-size={target}x{target}",
                str(input_path),
                str(page),
            ],
            ro=[input_path],
            rw=[temp_dir],
        )
        if not ok or not page.exists() or page.stat().st_size == 0:
            raise ThumbnailRenderError(f"ddjvu failed: {stderr}")
        await render_thumbnail_isolated(page, output_path, size=size, quality=quality)
    return True


# ── Video and audio ──────────────────────────────────────────────────────────


async def _video_streams(input_path: Path) -> list[dict[str, Any]]:
    """List the file's video streams, cover art included, with ffprobe."""
    async with _get_concurrency_guard("subprocess"):
        process = await async_sandboxed_run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v",
                "-show_entries",
                "stream=index:stream_disposition=attached_pic",
                "-of",
                "json",
                str(input_path),
            ],
            ro_paths=[input_path],
            timeout=30,
        )
    if process.returncode != 0:
        stderr = (process.stderr or b"").decode(errors="replace")[:300]
        raise ThumbnailRenderError(f"ffprobe failed: {stderr}")
    try:
        streams = json.loads(process.stdout or b"{}").get("streams", [])
    except (json.JSONDecodeError, AttributeError) as exc:
        raise ThumbnailRenderError("ffprobe returned invalid JSON") from exc
    return [s for s in streams if isinstance(s, dict) and isinstance(s.get("index"), int)]


async def _thumbnail_av(
    input_path: Path, output_path: Path, size: tuple[int, int], quality: int, mime: str
) -> bool:
    """Grab a video frame 2 s in (or the first frame), or the embedded cover art.

    Videos prefer a real frame and audio prefers its cover. Audio without cover
    art, and audio-only containers labelled as video, get no thumbnail.
    """
    streams = await _video_streams(input_path)
    covers = [s for s in streams if (s.get("disposition") or {}).get("attached_pic")]
    frames = [s for s in streams if s not in covers]
    seeks: tuple[str | None, ...]
    if frames and (mime.startswith("video/") or not covers):
        stream_map, seeks = f"0:{frames[0]['index']}", ("2", "0")
    elif covers:
        stream_map, seeks = f"0:{covers[0]['index']}", (None,)
    else:
        return False

    stderr = ""
    with processing_temp_dir(prefix="av-thumb-") as temp_dir:
        # Lossless intermediate; the only lossy encode is the final WebP.
        frame = temp_dir / "frame.png"
        for seek in seeks:
            ok, stderr = await _run_tool(
                [
                    "ffmpeg",
                    "-y",
                    *(["-ss", seek] if seek else []),
                    "-i",
                    str(input_path),
                    "-map",
                    stream_map,
                    "-frames:v",
                    "1",
                    "-vf",
                    f"scale=w={size[0]}:h={size[1]}:force_original_aspect_ratio=decrease",
                    "-f",
                    "image2",
                    str(frame),
                ],
                ro=[input_path],
                rw=[temp_dir],
            )
            if ok and frame.exists():
                await render_thumbnail_isolated(frame, output_path, size=size, quality=quality)
                return True
            frame.unlink(missing_ok=True)
    raise ThumbnailRenderError(f"ffmpeg could not extract a frame: {stderr}")


# ── PDF, Office and EPUB ─────────────────────────────────────────────────────


async def _thumbnail_pdf(
    input_path: Path, output_path: Path, size: tuple[int, int], quality: int, _mime: str = ""
) -> bool:
    """Render page 1, or page 2 when page 1 is blank (cover sheets, attestations).

    Each page renders to its own file, so a failing page 2 never loses page 1.
    ``-scale-to`` bounds the bitmap whatever the page size, so posters and CAD
    sheets never produce huge intermediates.
    """
    scale_to = str(round(max(size) * _SUPERSAMPLE))
    with processing_temp_dir(prefix="pdf-thumb-") as temp_dir:
        fallback: Path | None = None
        for page in (1, 2):
            stem = temp_dir / f"page-{page}"
            png = stem.with_suffix(".png")
            ok, stderr = await _run_tool(
                [
                    "pdftoppm",
                    "-png",
                    "-singlefile",
                    "-f",
                    str(page),
                    "-l",
                    str(page),
                    "-scale-to",
                    scale_to,
                    str(input_path),
                    str(stem),
                ],
                ro=[input_path],
                rw=[temp_dir],
            )
            if not ok or not png.exists():
                if page == 1:
                    raise ThumbnailRenderError(f"pdftoppm failed: {stderr}")
                break  # Single-page document: keep the blank page 1.
            candidate = temp_dir / f"page-{page}.webp"
            try:
                blank = await render_thumbnail_isolated(png, candidate, size=size, quality=quality)
            except (ValueError, RuntimeError):
                if page == 1:
                    raise
                break  # Page 2 is only a better candidate; keep page 1.
            if not blank:
                shutil.move(candidate, output_path)
                return True
            fallback = fallback or candidate
        if fallback is None:
            raise ThumbnailRenderError("PDF has no renderable page")
        shutil.move(fallback, output_path)
    return True


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


async def _soffice_to_pdf(source: Path, work_dir: Path, *, timeout: int = 120) -> Path | None:
    """Convert ``source`` (inside ``work_dir``) to PDF; return it, or None on failure.

    The private profile directory avoids lock collisions between concurrent jobs.
    """
    ok, stderr = await _run_tool(
        _soffice_command(
            f"-env:UserInstallation=file://{work_dir}",
            "--headless",
            "--norestore",
            "--nofirststartwizard",
            "--convert-to",
            "pdf",
            "--outdir",
            str(work_dir),
            str(source),
        ),
        ro=[],
        rw=[work_dir],
        timeout=timeout,
    )
    pdf = source.with_suffix(".pdf")
    if ok and pdf.is_file():
        return pdf
    logger.warning("LibreOffice produced no PDF for %s: %s", source.name, stderr)
    return None


async def _thumbnail_office(
    input_path: Path, output_path: Path, size: tuple[int, int], quality: int, mime: str
) -> bool:
    """Render page 1 through LibreOffice, else the largest embedded image."""
    with processing_temp_dir(prefix="office-thumb-") as work_dir:
        # LibreOffice picks its import filter from the extension.
        source = work_dir / f"document{OFFICE_MIME_SUFFIXES.get(mime, '.docx')}"
        await _shielded_to_thread(shutil.copy2, input_path, source)
        pdf = await _soffice_to_pdf(source, work_dir)
        if pdf is not None:
            try:
                return await _thumbnail_pdf(pdf, output_path, size, quality)
            except ThumbnailRenderError as exc:
                logger.warning("Converted PDF for %s did not render: %s", input_path.name, exc)

    # Legacy OLE2 files are not ZIP containers: the fallback rejects them.
    try:
        if await extract_office_thumbnail_isolated(
            input_path, output_path, size=size, quality=quality
        ):
            return True
    except ValueError as exc:
        logger.info("No embedded image fallback for %s: %s", input_path.name, exc)
    raise ThumbnailRenderError("Office document could not be rendered")


async def _thumbnail_epub(
    input_path: Path, output_path: Path, size: tuple[int, int], quality: int, _mime: str
) -> bool:
    return await extract_epub_cover_isolated(input_path, output_path, size=size, quality=quality)


def _renderer(kind: ThumbnailKind) -> Renderer:
    # Looked up at call time so tests can patch individual renderers.
    return {
        ThumbnailKind.IMAGE: _thumbnail_image,
        ThumbnailKind.SVG: _thumbnail_svg,
        ThumbnailKind.DJVU: _thumbnail_djvu,
        ThumbnailKind.VIDEO: _thumbnail_av,
        ThumbnailKind.AUDIO: _thumbnail_av,
        ThumbnailKind.PDF: _thumbnail_pdf,
        ThumbnailKind.OFFICE: _thumbnail_office,
        ThumbnailKind.EPUB: _thumbnail_epub,
    }[kind]
