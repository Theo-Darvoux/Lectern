"""PDF security checks and metadata stripping."""

import asyncio
import io
import logging
import tempfile
import zlib
from pathlib import Path
from typing import Final, cast

import pikepdf
from pikepdf.models.image import PdfImage
from PIL import Image

from app.config import settings
from app.core.security.file_security._concurrency import _get_concurrency_guard
from app.core.security.file_security._image import MAX_IMAGE_PIXELS
from app.core.security.sandbox import async_sandboxed_run

logger = logging.getLogger(__name__)

_PDF_DANGEROUS_ACTION_KEYS = frozenset(
    {
        "/AA",
        "/Launch",
        "/SubmitForm",
        "/ImportData",
    }
)

_PDF_ACTIVE_ANNOTATION_SUBTYPES = frozenset(
    {
        "/3D",
        "/FileAttachment",
        "/Movie",
        "/RichMedia",
        "/Screen",
        "/Sound",
    }
)

MAX_PDF_PAGES: Final[int] = 500
MAX_PDF_TREE_DEPTH: Final[int] = 50
MAX_OUTLINE_DEPTH: Final[int] = 100

_GS_QUALITY_TIERS: list[tuple[int, str, int, int, int]] = [
    (95, "/prepress", 300, 300, 1200),
    (85, "/printer", 200, 200, 600),
    (70, "/ebook", 96, 96, 300),
    (0, "/screen", 72, 72, 300),
]


def _walk_page_tree_for_actions(page_node: pikepdf.Dictionary, depth: int = 0) -> None:
    """Recursively walk the PDF page tree checking for dangerous actions."""
    if depth > MAX_PDF_TREE_DEPTH:
        raise ValueError("PDF page tree exceeds the maximum safe depth")
    for key in ("/AA", "/Launch", "/SubmitForm", "/ImportData"):
        if pikepdf.Name(key) in page_node:
            raise ValueError(f"PDF page contains dangerous action: {key}")
    if pikepdf.Name("/Kids") in page_node:
        kids = page_node["/Kids"]
        for i in range(len(kids)):
            _walk_page_tree_for_actions(cast(pikepdf.Dictionary, kids[i]), depth + 1)


def _validate_goto_action(
    action: pikepdf.Dictionary, *, context: str, depth: int = 0
) -> None:
    """Allow only local GoTo actions, including every chained /Next action."""
    if depth > MAX_PDF_TREE_DEPTH:
        raise ValueError(f"PDF {context} action chain exceeds the maximum safe depth")
    subtype = str(action.get("/S")) if action.get("/S") is not None else None
    if subtype != "/GoTo":
        raise ValueError(f"PDF {context} contains a dangerous action: {subtype or '/A'}")

    next_action = action.get("/Next")
    if next_action is None:
        return
    if isinstance(next_action, pikepdf.Dictionary):
        _validate_goto_action(next_action, context=context, depth=depth + 1)
        return
    if isinstance(next_action, pikepdf.Array):
        for chained in next_action:
            if not isinstance(chained, pikepdf.Dictionary):
                raise ValueError(f"PDF {context} contains a malformed /Next action")
            _validate_goto_action(chained, context=context, depth=depth + 1)
        return
    raise ValueError(f"PDF {context} contains a malformed /Next action")


def _check_interactive_actions(node: pikepdf.Dictionary, *, context: str) -> None:
    """Reject actions attached to annotations or AcroForm fields."""
    subtype = node.get("/Subtype")
    if subtype is not None and str(subtype) in _PDF_ACTIVE_ANNOTATION_SUBTYPES:
        raise ValueError(f"PDF {context} contains active content: {subtype}")
    if pikepdf.Name("/AA") in node:
        raise ValueError(f"PDF {context} contains a dangerous action: /AA")
    if pikepdf.Name("/A") in node:
        action = node["/A"]
        if not isinstance(action, pikepdf.Dictionary):
            raise ValueError(f"PDF {context} contains a malformed action: /A")
        _validate_goto_action(action, context=context)


def _walk_form_fields(fields: pikepdf.Array, depth: int = 0) -> None:
    if depth > MAX_PDF_TREE_DEPTH:
        raise ValueError("PDF form field tree exceeds the maximum safe depth")
    for raw_field in fields:
        if not isinstance(raw_field, pikepdf.Dictionary):
            raise ValueError("PDF contains a malformed form field")
        field = cast(pikepdf.Dictionary, raw_field)
        _check_interactive_actions(field, context="form field")
        kids = field.get("/Kids")
        if isinstance(kids, pikepdf.Array):
            _walk_form_fields(kids, depth + 1)


def _walk_outline_actions(
    node: pikepdf.Dictionary,
    *,
    strip: bool = False,
    depth: int = 0,
    seen: set[tuple[int, int]] | None = None,
) -> None:
    """Validate or strip actions from the linked outline tree."""
    if depth > MAX_OUTLINE_DEPTH:
        raise ValueError("PDF outline tree exceeds the maximum safe depth")
    if seen is None:
        seen = set()

    if getattr(node, "is_indirect", False):
        try:
            objgen = (int(node.objgen[0]), int(node.objgen[1]))
            if objgen != (0, 0):
                if objgen in seen:
                    return
                seen.add(objgen)
        except (ValueError, TypeError, AttributeError):
            pass

    if strip:
        for key in ("/A", "/AA"):
            if key in node:
                del node[key]
    else:
        _check_interactive_actions(node, context="outline")

    for key in ("/First", "/Next", "/Last"):
        child = node.get(key)
        if child is None:
            continue
        if not isinstance(child, pikepdf.Dictionary):
            raise ValueError(f"PDF contains a malformed outline link: {key}")
        _walk_outline_actions(
            cast(pikepdf.Dictionary, child),
            strip=strip,
            depth=depth + 1,
            seen=seen,
        )


def _strip_interactive_actions(node: pikepdf.Dictionary, depth: int = 0) -> None:
    if depth > 50:
        raise ValueError("PDF interactive object tree exceeds the maximum safe depth")
    for key in ("/A", "/AA"):
        if key in node:
            del node[key]
    kids = node.get("/Kids")
    if isinstance(kids, pikepdf.Array):
        for raw_child in kids:
            if isinstance(raw_child, pikepdf.Dictionary):
                _strip_interactive_actions(cast(pikepdf.Dictionary, raw_child), depth + 1)


def check_pdf_safety(file_path: Path) -> None:
    """Raise ValueError for PDFs with auto-executing, JavaScript constructs, or excessive pages."""
    try:
        with pikepdf.open(str(file_path), suppress_warnings=True) as pdf:
            if len(pdf.pages) > MAX_PDF_PAGES:
                raise ValueError(
                    f"PDF page count exceeds maximum safe limit ({len(pdf.pages)} > {MAX_PDF_PAGES})"
                )
            root = pdf.Root
            for key in _PDF_DANGEROUS_ACTION_KEYS:
                if pikepdf.Name(key) in root:
                    raise ValueError(
                        f"PDF contains auto-executing action ({key}) and cannot be uploaded."
                    )
            if pikepdf.Name("/OpenAction") in root:
                action = root["/OpenAction"]
                if isinstance(action, pikepdf.Dictionary):
                    try:
                        _validate_goto_action(action, context="/OpenAction")
                    except ValueError as exc:
                        raise ValueError(
                            "PDF contains a dangerous /OpenAction and cannot be uploaded."
                        ) from exc

            if pikepdf.Name("/Names") in root:
                names_tree = root["/Names"]
                if pikepdf.Name("/JavaScript") in names_tree:
                    raise ValueError("PDF contains embedded JavaScript and cannot be uploaded.")
            if pikepdf.Name("/Pages") in root:
                _walk_page_tree_for_actions(cast(pikepdf.Dictionary, root["/Pages"]))
            for page in pdf.pages:
                annotations = page.get("/Annots")
                if isinstance(annotations, pikepdf.Array):
                    for raw_annotation in annotations:
                        if not isinstance(raw_annotation, pikepdf.Dictionary):
                            raise ValueError("PDF contains a malformed annotation")
                        _check_interactive_actions(
                            cast(pikepdf.Dictionary, raw_annotation), context="annotation"
                        )
            acro_form = root.get("/AcroForm")
            if isinstance(acro_form, pikepdf.Dictionary):
                if pikepdf.Name("/XFA") in acro_form:
                    raise ValueError("PDF contains an XFA form and cannot be uploaded.")
                fields = acro_form.get("/Fields")
                if isinstance(fields, pikepdf.Array):
                    _walk_form_fields(fields)
            outlines = root.get("/Outlines")
            if isinstance(outlines, pikepdf.Dictionary):
                _walk_outline_actions(cast(pikepdf.Dictionary, outlines))
    except ValueError:
        raise
    except Exception as exc:
        logger.warning("PDF structure malformed, failing closed: %s", exc)
        raise ValueError(
            "File appears malformed or corrupted and cannot be validated for safety."
        ) from exc


def _apply_pdf_security_strip(pdf: pikepdf.Pdf) -> None:
    """Strip metadata and active-content constructs from an open pikepdf document"""
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
        annotations = page.get("/Annots")
        if isinstance(annotations, pikepdf.Array):
            for raw_annotation in annotations:
                if isinstance(raw_annotation, pikepdf.Dictionary):
                    _strip_interactive_actions(cast(pikepdf.Dictionary, raw_annotation))
    acro_form = catalog.get("/AcroForm")
    if isinstance(acro_form, pikepdf.Dictionary):
        if "/XFA" in acro_form:
            del acro_form["/XFA"]
        fields = acro_form.get("/Fields")
        if isinstance(fields, pikepdf.Array):
            for raw_field in fields:
                if isinstance(raw_field, pikepdf.Dictionary):
                    _strip_interactive_actions(cast(pikepdf.Dictionary, raw_field))
    outlines = catalog.get("/Outlines")
    if isinstance(outlines, pikepdf.Dictionary):
        _walk_outline_actions(cast(pikepdf.Dictionary, outlines), strip=True)


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
        raise ValueError("Failed to sanitize PDF metadata") from exc


async def _compress_pdf_ghostscript(file_path: Path, quality: int) -> Path:
    """Compress a PDF with Ghostscript's pdfwrite device."""
    # Pick the tier for the requested quality level
    profile, colour_dpi, gray_dpi, mono_dpi = "/ebook", 96, 96, 300
    for min_q, prof, cdpi, gdpi, mdpi in _GS_QUALITY_TIERS:
        if quality >= min_q:
            profile, colour_dpi, gray_dpi, mono_dpi = prof, cdpi, gdpi, mdpi
            break

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as _f:
        out_name = _f.name

    if quality >= 100:
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
        command = [
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
            "--",
            str(file_path),
        ]
        async with _get_concurrency_guard("subprocess"):
            proc = await async_sandboxed_run(
                command,
                ro_paths=[file_path],
                rw_paths=[out_name],
                timeout=120,
            )
        stderr = proc.stderr

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
    """Repack object/content streams with pikepdf. Returns True if output is smaller."""
    with pikepdf.open(str(file_path)) as pdf:
        if quality < 100:
            max_dim = 4096 if quality >= 85 else (2048 if quality >= 70 else 1600)
            for page in pdf.pages:
                for name, raw_image in page.images.items():
                    try:
                        _validate_pdf_image_dimensions(raw_image)
                        smask_ref = raw_image.get("/SMask")
                        mask_ref = raw_image.get("/Mask")
                        if isinstance(smask_ref, pikepdf.Stream):
                            _validate_pdf_image_dimensions(smask_ref)
                        if isinstance(mask_ref, pikepdf.Stream):
                            _validate_pdf_image_dimensions(mask_ref)

                        pdf_image = PdfImage(raw_image)
                        pil_image = pdf_image.as_pil_image()

                        if isinstance(smask_ref, pikepdf.Stream):
                            try:
                                smask_pdf_image = PdfImage(smask_ref)
                                smask_pil = smask_pdf_image.as_pil_image().convert("L")
                                if smask_pil.size != pil_image.size:
                                    smask_pil = smask_pil.resize(
                                        pil_image.size, Image.Resampling.LANCZOS
                                    )
                                pil_image = pil_image.convert("RGBA")
                                pil_image.putalpha(smask_pil)
                            except ValueError:
                                raise
                            except Exception as e:
                                logger.debug("Failed to apply SMask: %s", e)
                                continue
                        elif isinstance(mask_ref, pikepdf.Stream):
                            try:
                                mask_pdf_image = PdfImage(mask_ref)
                                mask_pil = mask_pdf_image.as_pil_image().convert("L")
                                if mask_pil.size != pil_image.size:
                                    mask_pil = mask_pil.resize(
                                        pil_image.size, Image.Resampling.LANCZOS
                                    )
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
                            except ValueError:
                                raise
                            except Exception as e:
                                logger.debug("Failed to apply stencil Mask: %s", e)
                                continue
                        elif isinstance(mask_ref, pikepdf.Array):
                            try:
                                mask_array = [int(x) for x in mask_ref]  # type: ignore[attr-defined]
                                bpc = int(str(raw_image.get("/BitsPerComponent", 8)))
                                max_val = (1 << bpc) - 1 if bpc in (1, 2, 4, 8, 16) else 255
                                if len(mask_array) == 6:
                                    r_min, r_max, g_min, g_max, b_min, b_max = mask_array
                                    r_min_8 = int((r_min / max_val) * 255)
                                    r_max_8 = int((r_max / max_val) * 255)
                                    g_min_8 = int((g_min / max_val) * 255)
                                    g_max_8 = int((g_max / max_val) * 255)
                                    b_min_8 = int((b_min / max_val) * 255)
                                    b_max_8 = int((b_max / max_val) * 255)

                                    pil_image = pil_image.convert("RGB")
                                    r, g, b = pil_image.split()
                                    r_m = r.point(lambda p: 255 if r_min_8 <= p <= r_max_8 else 0)
                                    g_m = g.point(lambda p: 255 if g_min_8 <= p <= g_max_8 else 0)
                                    b_m = b.point(lambda p: 255 if b_min_8 <= p <= b_max_8 else 0)
                                    from PIL import ImageChops

                                    trans = ImageChops.darker(ImageChops.darker(r_m, g_m), b_m)
                                    alpha = trans.point(lambda p: 255 - p)
                                    pil_image = pil_image.convert("RGBA")
                                    pil_image.putalpha(alpha)
                                elif len(mask_array) == 2:
                                    v_min, v_max = mask_array
                                    v_min_8 = int((v_min / max_val) * 255)
                                    v_max_8 = int((v_max / max_val) * 255)
                                    l_chan = pil_image.convert("L")
                                    trans = l_chan.point(
                                        lambda p: 255 if v_min_8 <= p <= v_max_8 else 0
                                    )
                                    alpha = trans.point(lambda p: 255 - p)
                                    pil_image = pil_image.convert("RGBA")
                                    pil_image.putalpha(alpha)
                            except Exception as e:
                                logger.debug("Failed to apply chroma key Mask: %s", e)
                                continue

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

                        has_alpha = pil_image.mode in ("RGBA", "LA")
                        smask = None
                        if has_alpha:
                            alpha_channel = pil_image.getchannel("A")
                            alpha_data = zlib.compress(alpha_channel.tobytes())
                            smask = pdf.make_stream(alpha_data)
                            smask.Type = pikepdf.Name("/XObject")
                            smask.Subtype = pikepdf.Name("/Image")
                            smask.Width = w
                            smask.Height = h
                            smask.ColorSpace = pikepdf.Name("/DeviceGray")
                            smask.BitsPerComponent = 8
                            smask.Filter = pikepdf.Name("/FlateDecode")

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
                        raw_image.Width = w
                        raw_image.Height = h
                        if "/DecodeParms" in raw_image:
                            del raw_image["/DecodeParms"]

                        if smask:
                            raw_image.SMask = smask
                        elif "/SMask" in raw_image:
                            del raw_image["/SMask"]

                        if "/Mask" in raw_image:
                            del raw_image["/Mask"]

                    except ValueError:
                        raise
                    except Exception as e:
                        logger.debug("Could not downsample PDF image %s: %s", name, e)

        pdf.save(
            out_name,
            compress_streams=True,
            object_stream_mode=pikepdf.ObjectStreamMode.generate,
            recompress_flate=(quality < 100),
        )

    return Path(out_name).stat().st_size < file_path.stat().st_size


def _validate_pdf_image_dimensions(image: pikepdf.Stream) -> None:
    """Reject oversized PDF image streams before any in-process decode."""
    try:
        width = int(str(image.get("/Width")))
        height = int(str(image.get("/Height")))
    except (TypeError, ValueError) as exc:
        raise ValueError("PDF contains an image with invalid dimensions") from exc
    pixels = width * height
    if width <= 0 or height <= 0 or pixels > MAX_IMAGE_PIXELS:
        raise ValueError(
            f"PDF embedded image exceeds pixel limit ({pixels:,} > {MAX_IMAGE_PIXELS:,})"
        )

_VECTOR_HEAVY_BYTES_PER_PAGE = 400 * 1024
_VECTOR_HEAVY_MAX_IMAGE_PIXELS = 500_000


def _is_vector_heavy_pdf(file_path: Path) -> bool:
    """Return True if this PDF is dominated by vector paths rather than raster images."""
    try:
        with pikepdf.open(str(file_path), suppress_warnings=True) as pdf:
            n_pages = len(pdf.pages)
            if n_pages == 0:
                return False

            file_size = file_path.stat().st_size
            if file_size / n_pages < _VECTOR_HEAVY_BYTES_PER_PAGE:
                return False

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


async def _rasterize_pdf_path(file_path: Path, quality: int = 85) -> Path:
    """Rasterize vector-heavy PDF pages to JPEG and repack as a new PDF."""
    if quality >= 85:
        dpi, jpeg_q = 300, 90
    elif quality >= 70:
        dpi, jpeg_q = 200, 80
    else:
        dpi, jpeg_q = 150, 70

    with tempfile.TemporaryDirectory() as tmpdir:
        page_prefix = str(Path(tmpdir) / "page")

        command = [
            "gs",
            "-dBATCH",
            "-dNOPAUSE",
            "-dQUIET",
            "-dSAFER",
            "-sDEVICE=jpeg",
            f"-r{dpi}",
            f"-dJPEGQ={jpeg_q}",
            f"-sOutputFile={page_prefix}-%03d.jpg",
            "--",
            str(file_path),
        ]
        async with _get_concurrency_guard("subprocess"):
            proc = await async_sandboxed_run(
                command,
                ro_paths=[file_path],
                rw_paths=[tmpdir],
                timeout=180,
            )
        stderr_bytes = proc.stderr or b""

        if proc.returncode != 0:
            logger.warning(
                "GS rasterize failed (rc=%d): %s",
                proc.returncode,
                stderr_bytes.decode(errors="replace")[:200],
            )
            return file_path

        # Sort using numeric key to handle 1000+ page documents correctly without page scrambling
        jpeg_paths = sorted(
            (str(p) for p in Path(tmpdir).glob("page-*.jpg")),
            key=lambda p: int(Path(p).stem.rsplit("-", 1)[1]),
        )
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
    """Three-stage PDF compression: Ghostscript, pikepdf, and optional rasterization"""
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
            raster_result = await _rasterize_pdf_path(file_path, quality=quality)
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
