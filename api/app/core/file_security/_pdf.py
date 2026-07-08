"""PDF security checks and metadata stripping.

Provides:
- check_pdf_safety: structural validation with pikepdf (OpenAction, JavaScript, etc.)
- _apply_pdf_security_strip: strip XMP, /Info, and active content from an open PDF
- _strip_pdf_from_path: path-based strip producing a new temp file
- _compress_pdf_path: three-stage compression: Ghostscript (font subsetting), pikepdf
  (object-stream packing), and optional rasterization for vector-heavy PDFs.
"""

import asyncio
import io
import logging
import tempfile
import zlib
from pathlib import Path
from typing import cast

import pikepdf
from pikepdf.models.image import PdfImage
from PIL import Image

from app.config import settings

logger = logging.getLogger(__name__)

_PDF_DANGEROUS_ACTION_KEYS = frozenset(
    {
        "/AA",
        "/Launch",
        "/SubmitForm",
        "/ImportData",
    }
)

# Action subtypes that make an /OpenAction dangerous.
# /GoTo and /GoToR are navigation-only; everything else that can execute code or
# open external resources is blocked.
_DANGEROUS_OPEN_ACTION_SUBTYPES = frozenset(
    {
        "/JavaScript",
        "/JS",
        "/Launch",
        "/URI",
        "/SubmitForm",
        "/ImportData",
        "/RichMediaExecute",
    }
)

# Map quality tiers to (PDFSETTINGS profile, colour dpi, gray dpi, mono dpi).
# Explicit DPI values override the profile defaults and give fine-grained control.
# At quality=75 (default) we target 96 dpi — matching ilovepdf "recommended" output.
_GS_QUALITY_TIERS: list[tuple[int, str, int, int, int]] = [
    # (min_quality, profile,      colour_dpi, gray_dpi, mono_dpi)  # noqa: ERA001
    (95, "/prepress", 300, 300, 1200),
    (85, "/printer", 200, 200, 600),
    (70, "/ebook", 96, 96, 300),
    (0, "/screen", 72, 72, 300),
]


def _walk_page_tree_for_actions(page_node: pikepdf.Dictionary, depth: int = 0) -> None:
    """Recursively walk the PDF page tree checking for dangerous actions."""
    if depth > 50:
        return  # Guard against circular references
    for key in ("/AA", "/Launch", "/SubmitForm", "/ImportData"):
        if pikepdf.Name(key) in page_node:
            raise ValueError(f"PDF page contains dangerous action: {key}")
    if pikepdf.Name("/Kids") in page_node:
        kids = page_node["/Kids"]
        for i in range(len(kids)):
            _walk_page_tree_for_actions(cast(pikepdf.Dictionary, kids[i]), depth + 1)


def check_pdf_safety(file_path: Path) -> None:
    """Raise ValueError for PDFs with auto-executing or JavaScript constructs.

    Checks the document catalog Root for dangerous action keys
    (``/OpenAction``, ``/AA``, ``/Launch``, ``/GoToR``, ``/URI``,
    ``/SubmitForm``, ``/ImportData``), the Names tree for ``/JavaScript``,
    and recursively walks the page tree for per-page action dictionaries.

    Fails open: if pikepdf cannot parse the file, we let YARA handle it.
    Raises ValueError with a human-readable message on detection so the
    worker can report MALICIOUS status.
    """
    try:
        with pikepdf.open(str(file_path), suppress_warnings=True) as pdf:
            root = pdf.Root
            for key in _PDF_DANGEROUS_ACTION_KEYS:
                if pikepdf.Name(key) in root:
                    raise ValueError(
                        f"PDF contains auto-executing action ({key}) and cannot be uploaded."
                    )
            if pikepdf.Name("/OpenAction") in root:
                action = root["/OpenAction"]
                if isinstance(action, pikepdf.Dictionary):
                    # Action dict: check the /S subtype. A missing /S is treated as dangerous
                    # (malformed action with unknown behaviour).
                    s = action.get("/S")
                    subtype = str(s) if s is not None else None
                    if subtype is None or subtype in _DANGEROUS_OPEN_ACTION_SUBTYPES:
                        raise ValueError(
                            f"PDF contains a dangerous /OpenAction ({subtype}) and cannot be uploaded."
                        )
                # Non-dictionary /OpenAction is a destination reference (array or name string)
                # that scrolls to a page — it cannot execute code and is safe to allow.
            if pikepdf.Name("/Names") in root:
                names_tree = root["/Names"]
                if pikepdf.Name("/JavaScript") in names_tree:
                    raise ValueError("PDF contains embedded JavaScript and cannot be uploaded.")
            if pikepdf.Name("/Pages") in root:
                _walk_page_tree_for_actions(cast(pikepdf.Dictionary, root["/Pages"]))
    except ValueError:
        raise
    except Exception as exc:
        logger.warning("PDF structure malformed, failing closed: %s", exc)
        raise ValueError(
            "File appears malformed or corrupted and cannot be validated for safety."
        ) from exc


def _apply_pdf_security_strip(pdf: pikepdf.Pdf) -> None:
    """Strip metadata and active-content constructs from an open pikepdf document.

    Removes: XMP stream, /Info dict, /OpenAction, catalog /AA,
    /Names//EmbeddedFiles, and per-page /AA entries.
    """
    with pdf.open_metadata():
        pass
    if "/Info" in pdf.trailer:
        del pdf.trailer["/Info"]
    catalog = pdf.Root
    if "/OpenAction" in catalog:
        del catalog["/OpenAction"]
    if "/AA" in catalog:
        del catalog["/AA"]
    if "/Names" in catalog:
        names = catalog["/Names"]
        if "/EmbeddedFiles" in names:
            del names["/EmbeddedFiles"]
        if "/JavaScript" in names:
            del names["/JavaScript"]
    for page in pdf.pages:
        if "/AA" in page:
            del page["/AA"]  # type: ignore[operator]  # pikepdf stubs


def _strip_pdf_from_path(file_path: Path) -> Path:
    """Remove Document Info, XMP metadata, and active content from PDFs on disk."""
    new_path = None
    try:
        with pikepdf.open(str(file_path)) as pdf:
            _apply_pdf_security_strip(pdf)
            with tempfile.NamedTemporaryFile(delete=False) as _f:
                new_path = _f.name
            pdf.save(new_path)
            return Path(new_path)
    except Exception as exc:
        logger.warning("PDF metadata strip path failed: %s", exc)
        if new_path is not None:
            Path(new_path).unlink(missing_ok=True)
        return file_path


async def _compress_pdf_ghostscript(file_path: Path, quality: int) -> Path:
    """Compress a PDF with Ghostscript's pdfwrite device.

    Ghostscript subsets embedded fonts and resamples images — the two dominant
    sources of PDF bloat that pikepdf cannot touch. Returns a new temp path if
    the result is smaller than the input; returns the original path on failure
    or if no saving was achieved (fail-open).

    At quality >= 100 the image stages are disabled entirely: GS still subsets
    fonts and packs objects (both lossless) but passes image streams through
    untouched, so no resampling or JPEG re-encoding occurs.
    """
    # Pick the tier for the requested quality level
    profile, colour_dpi, gray_dpi, mono_dpi = "/ebook", 96, 96, 300
    for min_q, prof, cdpi, gdpi, mdpi in _GS_QUALITY_TIERS:
        if quality >= min_q:
            profile, colour_dpi, gray_dpi, mono_dpi = prof, cdpi, gdpi, mdpi
            break

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as _f:
        out_name = _f.name

    if quality >= 100:
        # Lossless image mode: subset fonts and pack objects, but pass image
        # streams through untouched — no downsampling and no re-encoding. Without
        # these flags the /prepress profile would still resample to 300 dpi and
        # re-JPEG colour images (AutoFilter), losing quality even at quality=100.
        image_args = [
            "-dDownsampleColorImages=false",
            "-dDownsampleGrayImages=false",
            "-dDownsampleMonoImages=false",
            "-dAutoFilterColorImages=false",
            "-dAutoFilterGrayImages=false",
            "-dEncodeColorImages=false",
            "-dEncodeGrayImages=false",
            "-dEncodeMonoImages=false",
        ]
    else:
        # Explicit DPI overrides — these win over the profile defaults.
        image_args = [
            "-dDownsampleColorImages=true",
            "-dColorImageDownsampleType=/Bicubic",
            f"-dColorImageResolution={colour_dpi}",
            "-dDownsampleGrayImages=true",
            "-dGrayImageDownsampleType=/Bicubic",
            f"-dGrayImageResolution={gray_dpi}",
            "-dDownsampleMonoImages=true",
            "-dMonoImageDownsampleType=/Subsample",
            f"-dMonoImageResolution={mono_dpi}",
        ]

    try:
        proc = await asyncio.create_subprocess_exec(
            "gs",
            "-dBATCH",
            "-dNOPAUSE",
            "-dQUIET",
            "-dSAFER",
            "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.4",
            f"-dPDFSETTINGS={profile}",
            "-dDetectDuplicateImages=true",
            *image_args,
            f"-sOutputFile={out_name}",
            str(file_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120.0)

        if proc.returncode != 0:
            logger.warning(
                "Ghostscript failed (rc=%d): %s",
                proc.returncode,
                stderr.decode(errors="replace")[:300],
            )
            Path(out_name).unlink(missing_ok=True)
            return file_path

        out_size = Path(out_name).stat().st_size
        if out_size < file_path.stat().st_size:
            logger.debug(
                "Ghostscript: %d → %d bytes (%.0f%%)",
                file_path.stat().st_size,
                out_size,
                100 * out_size / file_path.stat().st_size,
            )
            return Path(out_name)

        Path(out_name).unlink(missing_ok=True)
        return file_path

    except Exception as exc:
        Path(out_name).unlink(missing_ok=True)
        logger.warning("Ghostscript compression error: %s", exc)
        return file_path


def _pikepdf_repack_streams(file_path: Path, out_name: str, quality: int) -> bool:
    """Repack object/content streams with pikepdf. Returns True if output is smaller.

    When called after Ghostscript, image processing is intentionally skipped —
    GS has already resampled and re-encoded images. pikepdf's role here is solely
    to generate object streams (PDF 1.5 cross-reference streams) and recompress
    any remaining FlateDecode streams that GS left unoptimised.

    When called without a prior GS pass (GS unavailable or produced no gain),
    full image downsampling is performed in addition to stream repacking.
    """
    with pikepdf.open(str(file_path)) as pdf:
        if quality >= 100:
            max_dim = 4096
        elif quality >= 85:
            max_dim = 2048
        elif quality >= 70:
            max_dim = 1600
        else:
            max_dim = 1024

        for page in pdf.pages:
            for name, raw_image in page.images.items():
                try:
                    pdf_image = PdfImage(raw_image)
                    pil_image = pdf_image.as_pil_image()

                    # Reconstruct transparency from /SMask or /Mask. Since pikepdf extracts
                    # the base image stream directly, the transparency is not automatically
                    # composited. We load the mask and apply it to pil_image.
                    smask_ref = raw_image.get("/SMask")
                    mask_ref = raw_image.get("/Mask")

                    if isinstance(smask_ref, pikepdf.Stream):
                        try:
                            smask_pdf_image = PdfImage(smask_ref)
                            smask_pil = smask_pdf_image.as_pil_image()
                            smask_pil = smask_pil.convert("L")
                            if smask_pil.size != pil_image.size:
                                smask_pil = smask_pil.resize(
                                    pil_image.size, Image.Resampling.LANCZOS
                                )
                            pil_image = pil_image.convert("RGBA")
                            pil_image.putalpha(smask_pil)
                        except Exception as e:
                            logger.debug("Failed to apply SMask: %s", e)
                    elif isinstance(mask_ref, pikepdf.Stream):
                        try:
                            mask_pdf_image = PdfImage(mask_ref)
                            mask_pil = mask_pdf_image.as_pil_image()
                            mask_pil = mask_pil.convert("L")
                            if mask_pil.size != pil_image.size:
                                mask_pil = mask_pil.resize(pil_image.size, Image.Resampling.LANCZOS)
                            decode = mask_ref.get("/Decode")
                            if (
                                decode is not None
                                and len(decode) >= 2
                                and float(decode[0]) > float(decode[1])
                            ):
                                from PIL import ImageOps

                                mask_pil = ImageOps.invert(mask_pil)
                            pil_image = pil_image.convert("RGBA")
                            pil_image.putalpha(mask_pil)
                        except Exception as e:
                            logger.debug("Failed to apply stencil Mask: %s", e)
                    elif isinstance(mask_ref, pikepdf.Array):
                        try:
                            mask_array = [int(x) for x in mask_ref]  # type: ignore[attr-defined]
                            if len(mask_array) == 6:
                                r_min, r_max, g_min, g_max, b_min, b_max = mask_array
                                pil_image = pil_image.convert("RGB")
                                r, g, b = pil_image.split()
                                r_mask = r.point(
                                    lambda p, r_min=r_min, r_max=r_max: (
                                        255 if r_min <= p <= r_max else 0
                                    )
                                )
                                g_mask = g.point(
                                    lambda p, g_min=g_min, g_max=g_max: (
                                        255 if g_min <= p <= g_max else 0
                                    )
                                )
                                b_mask = b.point(
                                    lambda p, b_min=b_min, b_max=b_max: (
                                        255 if b_min <= p <= b_max else 0
                                    )
                                )
                                from PIL import ImageChops

                                transparent_mask = ImageChops.darker(r_mask, g_mask)
                                transparent_mask = ImageChops.darker(transparent_mask, b_mask)
                                alpha_mask = transparent_mask.point(lambda p: 255 - p)
                                pil_image = pil_image.convert("RGBA")
                                pil_image.putalpha(alpha_mask)
                            elif len(mask_array) == 2:
                                v_min, v_max = mask_array
                                l_chan = pil_image.convert("L")
                                transparent_mask = l_chan.point(
                                    lambda p, v_min=v_min, v_max=v_max: (
                                        255 if v_min <= p <= v_max else 0
                                    )
                                )
                                alpha_mask = transparent_mask.point(lambda p: 255 - p)
                                pil_image = pil_image.convert("RGBA")
                                pil_image.putalpha(alpha_mask)
                        except Exception as e:
                            logger.debug("Failed to apply chroma key Mask: %s", e)

                    if pil_image.width < 100 or pil_image.height < 100:
                        continue

                    w, h = pil_image.size
                    needs_resize = w > max_dim or h > max_dim

                    existing_filter = raw_image.get("/Filter")
                    already_jpeg = existing_filter == pikepdf.Name("/DCTDecode")
                    if already_jpeg and not needs_resize:
                        continue
                    if needs_resize:
                        ratio = min(max_dim / w, max_dim / h)
                        w = int(w * ratio)
                        h = int(h * ratio)
                        pil_image = pil_image.resize((w, h), Image.Resampling.LANCZOS)

                    # Handle transparency by creating/updating Soft Mask (SMask)
                    has_alpha = pil_image.mode in ("RGBA", "LA")
                    smask = None
                    if has_alpha:
                        alpha_channel = pil_image.getchannel("A")
                        # Alpha channel is always saved with FlateDecode (lossless)
                        alpha_data = zlib.compress(alpha_channel.tobytes())
                        smask = pdf.make_stream(alpha_data)
                        smask.Type = pikepdf.Name("/XObject")
                        smask.Subtype = pikepdf.Name("/Image")
                        smask.Width = w
                        smask.Height = h
                        smask.ColorSpace = pikepdf.Name("/DeviceGray")
                        smask.BitsPerComponent = 8
                        smask.Filter = pikepdf.Name("/FlateDecode")

                    # Decide on compression strategy for the main image data
                    # Line art (low unique color count) uses FlateDecode (lossless)
                    # Photos/gradients use DCTDecode (lossy JPEG)
                    sample = pil_image.convert("RGB").resize((min(w, 64), min(h, 64)))
                    unique_colors = len(set(sample.getdata()))
                    is_line_art = unique_colors < 256

                    if is_line_art:
                        # Convert to base mode (RGB or L) for the stream
                        if pil_image.mode in ("RGBA", "RGB"):
                            img_to_save = pil_image.convert("RGB")
                            raw_image.ColorSpace = pikepdf.Name("/DeviceRGB")
                        else:
                            img_to_save = pil_image.convert("L")
                            raw_image.ColorSpace = pikepdf.Name("/DeviceGray")

                        img_data = zlib.compress(img_to_save.tobytes())
                        raw_image.write(img_data, filter=pikepdf.Name("/FlateDecode"))
                        raw_image.BitsPerComponent = 8
                    else:
                        # Photo: JPEG compression
                        if pil_image.mode in ("RGBA", "RGB"):
                            img_to_save = pil_image.convert("RGB")
                            raw_image.ColorSpace = pikepdf.Name("/DeviceRGB")
                        else:
                            img_to_save = pil_image.convert("L")
                            raw_image.ColorSpace = pikepdf.Name("/DeviceGray")

                        buf = io.BytesIO()
                        img_to_save.save(buf, format="JPEG", quality=quality, optimize=True)
                        raw_image.write(buf.getvalue(), filter=pikepdf.Name("/DCTDecode"))
                        raw_image.BitsPerComponent = 8

                    # Update common metadata
                    raw_image.Width = w
                    raw_image.Height = h
                    if "/DecodeParms" in raw_image:
                        del raw_image["/DecodeParms"]

                    # Link the SMask if we have one, otherwise ensure any old one is removed
                    if smask:
                        raw_image.SMask = smask
                    elif "/SMask" in raw_image:
                        del raw_image["/SMask"]

                    # Remove old /Mask key to avoid conflicts with new SMask/image data
                    if "/Mask" in raw_image:
                        del raw_image["/Mask"]

                except Exception as e:
                    logger.debug("Could not downsample PDF image %s: %s", name, e)

        pdf.save(
            out_name,
            compress_streams=True,
            object_stream_mode=pikepdf.ObjectStreamMode.generate,
            recompress_flate=(quality < 100),
        )

    return Path(out_name).stat().st_size < file_path.stat().st_size


# Per-page compressed content stream size above which a PDF is considered vector-heavy.
# A typical A4 page of text compresses to ~5–50 KB; SVG-derived pages with thousands
# of bezier curves compress to 500 KB–5 MB. We use 400 KB/page as the threshold.
_VECTOR_HEAVY_BYTES_PER_PAGE = 400 * 1024

# If average raster image coverage is below this pixel count per page, the document
# has little raster content and the bulk of the data is vector paths.
_VECTOR_HEAVY_MAX_IMAGE_PIXELS = 500_000


def _is_vector_heavy_pdf(file_path: Path) -> bool:
    """Return True if this PDF is dominated by vector paths rather than raster images.

    Vector-heavy PDFs (e.g. SVG-derived diagrams exported from macOS) contain thousands
    of bezier curves per page that compress poorly with the normal GS font-subsetting
    pipeline. Rasterization is a better strategy for these files.
    """
    try:
        with pikepdf.open(str(file_path), suppress_warnings=True) as pdf:
            n_pages = len(pdf.pages)
            if n_pages == 0:
                return False

            file_size = file_path.stat().st_size
            if file_size / n_pages < _VECTOR_HEAVY_BYTES_PER_PAGE:
                return False

            # Count raster pixel coverage; vector-heavy PDFs have few or no embedded images
            total_pixels = 0
            for page in pdf.pages:
                for _, img in page.images.items():
                    try:
                        w = int(img.get("/Width", 0))
                        h = int(img.get("/Height", 0))
                        total_pixels += w * h
                    except Exception:
                        pass

            avg_pixels_per_page = total_pixels / n_pages
            return avg_pixels_per_page < _VECTOR_HEAVY_MAX_IMAGE_PIXELS
    except Exception:
        return False


def _build_rasterized_pdf(jpeg_paths: list[str], out_path: str, dpi: int) -> bool:
    """Pack JPEG page images into a PDF using pikepdf. Returns True on success."""
    pts_per_pixel = 72.0 / dpi
    pdf = pikepdf.new()

    for jpg_path in jpeg_paths:
        with Image.open(jpg_path) as img:
            w, h = img.size

        pts_w = w * pts_per_pixel
        pts_h = h * pts_per_pixel

        with open(jpg_path, "rb") as f:
            jpeg_data = f.read()

        img_stream = pdf.make_stream(jpeg_data)
        img_stream.Type = pikepdf.Name("/XObject")
        img_stream.Subtype = pikepdf.Name("/Image")
        img_stream.Width = w
        img_stream.Height = h
        img_stream.ColorSpace = pikepdf.Name("/DeviceRGB")
        img_stream.BitsPerComponent = 8
        img_stream.Filter = pikepdf.Name("/DCTDecode")

        # Use pikepdf.Name as key — **{"/Im1": ...} produces "//Im1" (double slash)
        xobj = pikepdf.Dictionary()
        xobj[pikepdf.Name("/Im1")] = img_stream

        content = f"q {pts_w} 0 0 {pts_h} 0 0 cm /Im1 Do Q".encode()
        page_dict = pikepdf.Dictionary(
            Type=pikepdf.Name("/Page"),
            MediaBox=[0, 0, pts_w, pts_h],
            Resources=pikepdf.Dictionary(XObject=xobj),
            Contents=pdf.make_stream(content),
        )
        pdf.pages.append(pikepdf.Page(pdf.make_indirect(page_dict)))

    pdf.save(out_path, compress_streams=True)
    return True


async def _rasterize_pdf_path(file_path: Path, dpi: int = 400) -> Path:
    """Rasterize vector-heavy PDF pages to JPEG and repack as a new PDF.

    Uses Ghostscript's jpeg device to render each page at ``dpi`` DPI (quality 90),
    then uses pikepdf to reassemble them into a compact PDF. This reduces file size
    by 5–15× for PDFs dominated by SVG-derived bezier paths that resist normal
    font-subsetting / stream-packing compression.

    Returns a new temp path when the result is smaller; original path otherwise.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        page_prefix = str(Path(tmpdir) / "page")

        proc = await asyncio.create_subprocess_exec(
            "gs",
            "-dBATCH",
            "-dNOPAUSE",
            "-dQUIET",
            "-dSAFER",
            "-sDEVICE=jpeg",
            f"-r{dpi}",
            "-dJPEGQ=90",
            f"-sOutputFile={page_prefix}-%03d.jpg",
            str(file_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=180.0)

        if proc.returncode != 0:
            logger.warning(
                "GS rasterize failed (rc=%d): %s",
                proc.returncode,
                stderr.decode(errors="replace")[:200],
            )
            return file_path

        jpeg_paths = sorted(str(p) for p in Path(tmpdir).glob("page-*.jpg"))
        if not jpeg_paths:
            return file_path

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as _f:
            out_name = _f.name

        try:
            ok = await asyncio.to_thread(_build_rasterized_pdf, jpeg_paths, out_name, dpi)
            if not ok:
                Path(out_name).unlink(missing_ok=True)
                return file_path

            out_size = Path(out_name).stat().st_size
            orig_size = file_path.stat().st_size
            if out_size < orig_size:
                logger.debug(
                    "PDF rasterize: %d → %d bytes (%.0f%%)",
                    orig_size,
                    out_size,
                    100 * out_size / orig_size,
                )
                return Path(out_name)

            Path(out_name).unlink(missing_ok=True)
            return file_path

        except Exception as exc:
            Path(out_name).unlink(missing_ok=True)
            logger.warning("PDF rasterize reassembly failed: %s", exc)
            return file_path


async def _compress_pdf_path(file_path: Path, config: dict | None = None) -> Path:  # type: ignore[type-arg]
    """Three-stage PDF compression: Ghostscript, pikepdf, and optional rasterization.

    Stage 1 — Ghostscript:
      Subsets embedded fonts and resamples images. This is the dominant compression
      lever for typical academic/conference PDFs where unsubsetted fonts account for
      the majority of file size. Fail-open: if gs is unavailable or produces no gain,
      stage 2 runs on the original file with full image processing instead.

    Stage 2 — pikepdf:
      Packs objects into cross-reference streams (PDF 1.5) and recompresses
      FlateDecode streams. When GS already ran, image processing is skipped to
      avoid generation loss. When GS was skipped, full image downsampling runs here.

    Stage 3 — rasterization (vector-heavy PDFs only):
      When stages 1+2 achieve no meaningful gain and the PDF is vector-heavy
      (large compressed content streams, few raster images — typical of SVG-derived
      diagrams exported from macOS), each page is rendered to JPEG via Ghostscript
      and repacked. Reduces file size by 10–20× for these files.

    Returns the smallest result ≤ the original, or the original if no stage helped.
    """
    cfg_quality = config.get("pdf_quality") if config else None
    quality = cfg_quality if cfg_quality is not None else settings.pdf_quality

    # Stage 1: Ghostscript
    gs_result = await _compress_pdf_ghostscript(file_path, quality)
    gs_improved = gs_result != file_path

    # Stage 2: pikepdf stream repacking on the GS output (or original).
    # When GS ran, skip image processing (GS already handled it); just repack streams.
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as _f:
        out_name = _f.name

    best_path = file_path
    try:
        work_path = gs_result  # GS output, or original if GS produced no gain

        smaller = await asyncio.to_thread(
            _pikepdf_repack_streams,
            work_path,
            out_name,
            quality if not gs_improved else 100,  # quality=100 → stream-only, no image processing
        )

        # Clean up the intermediate GS file if pikepdf further reduced it
        if gs_improved and smaller:
            gs_result.unlink(missing_ok=True)

        if smaller:
            best_path = Path(out_name)
        else:
            Path(out_name).unlink(missing_ok=True)
            best_path = gs_result  # GS result alone (may equal file_path if GS also failed)

    except Exception:
        Path(out_name).unlink(missing_ok=True)
        if gs_improved:
            best_path = gs_result
        else:
            raise

    # Stage 3: rasterization for vector-heavy PDFs that resisted stages 1+2.
    # Only triggered when the best result so far is still ≥80% of original size.
    try:
        best_size = best_path.stat().st_size
        orig_size = file_path.stat().st_size
        if best_size >= orig_size * 0.8 and await asyncio.to_thread(
            _is_vector_heavy_pdf, file_path
        ):
            raster_result = await _rasterize_pdf_path(file_path)
            if raster_result != file_path:
                raster_size = raster_result.stat().st_size
                if raster_size < best_size:
                    if best_path != file_path:
                        best_path.unlink(missing_ok=True)
                    return raster_result
                raster_result.unlink(missing_ok=True)
    except Exception:
        if best_path != file_path:
            best_path.unlink(missing_ok=True)
        raise

    return best_path
