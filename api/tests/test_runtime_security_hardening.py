"""Focused regressions for runtime and embedded-metadata security boundaries."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from app.core.security.file_security._jpeg import strip_jpeg_metadata
from app.core.security.file_security._svg import SvgSecurityError, check_svg_safety
from app.core.security.file_security.errors import SanitizationError


def _jpeg_metadata_segment(payload: bytes) -> bytes:
    body = b"Exif\x00\x00" + payload
    return b"\xff\xe1" + (len(body) + 2).to_bytes(2, "big") + body


def test_jpeg_scrubber_removes_header_and_between_scan_metadata() -> None:
    image = Image.new("RGB", (64, 64), (20, 100, 200))
    buffer = BytesIO()
    image.save(
        buffer,
        format="JPEG",
        progressive=True,
        exif=b"Exif\x00\x00private-header",
    )
    source = buffer.getvalue()

    scan_positions: list[int] = []
    start = 0
    while (position := source.find(b"\xff\xda", start)) >= 0:
        scan_positions.append(position)
        start = position + 2
    assert len(scan_positions) >= 2

    injected = (
        source[: scan_positions[1]]
        + _jpeg_metadata_segment(b"between-scans")
        + source[scan_positions[1] :]
    )
    cleaned = strip_jpeg_metadata(injected)

    assert b"private-header" not in cleaned
    assert b"between-scans" not in cleaned
    with Image.open(BytesIO(cleaned)) as decoded:
        decoded.load()
        assert decoded.size == (64, 64)


def test_jpeg_scrubber_rejects_malformed_segments() -> None:
    with pytest.raises(SanitizationError, match="invalid length"):
        strip_jpeg_metadata(b"\xff\xd8\xff\xe1\x00\x01")


def test_svg_allows_local_gradient_in_style() -> None:
    check_svg_safety(
        b"""<svg xmlns="http://www.w3.org/2000/svg">
        <defs><linearGradient id="gradient"/></defs>
        <rect style="fill:url(#gradient); stroke:#fff"/>
        </svg>"""
    )


@pytest.mark.parametrize(
    "payload",
    [
        b"""<svg xmlns="http://www.w3.org/2000/svg"><rect style="fill:j\\61vascript:alert(1)"/></svg>""",
        b"""<svg xmlns="http://www.w3.org/2000/svg"><rect style="fill:u\\72l(https://example.com/a)"/></svg>""",
        b"""<svg xmlns="http://www.w3.org/2000/svg"><a href="java\\73cript:alert(1)"/></svg>""",
    ],
)
def test_svg_rejects_css_escaped_active_content(payload: bytes) -> None:
    with pytest.raises(SvgSecurityError):
        check_svg_safety(payload)


@pytest.mark.asyncio
async def test_pdf_compression_preserves_sanitization_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("pikepdf")
    from app.core.security.file_security import _pdf

    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.4\n")

    async def no_ghostscript(path: Path, _quality: int) -> Path:
        return path

    def reject_image(*_args: object, **_kwargs: object) -> bool:
        raise SanitizationError("oversized embedded image")

    monkeypatch.setattr(_pdf, "_compress_pdf_ghostscript", no_ghostscript)
    monkeypatch.setattr(_pdf, "_pikepdf_repack_streams", reject_image)

    with pytest.raises(SanitizationError, match="oversized embedded image"):
        await _pdf._compress_pdf_path(source)


def test_pdf_strip_removes_embedded_jpeg_metadata(tmp_path: Path) -> None:
    pikepdf = pytest.importorskip("pikepdf")
    from app.core.security.file_security._pdf import _strip_pdf_from_path

    image = Image.new("RGB", (32, 32), (100, 20, 200))
    jpeg_buffer = BytesIO()
    image.save(
        jpeg_buffer,
        format="JPEG",
        exif=b"Exif\x00\x00embedded-private-metadata",
    )

    source = tmp_path / "embedded.pdf"
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(32, 32))
    image_stream = pdf.make_stream(jpeg_buffer.getvalue())
    image_stream.Type = pikepdf.Name("/XObject")
    image_stream.Subtype = pikepdf.Name("/Image")
    image_stream.Width = 32
    image_stream.Height = 32
    image_stream.ColorSpace = pikepdf.Name("/DeviceRGB")
    image_stream.BitsPerComponent = 8
    image_stream.Filter = pikepdf.Name("/DCTDecode")
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im1=image_stream))
    page.Contents = pdf.make_stream(b"q 32 0 0 32 0 0 cm /Im1 Do Q")
    pdf.save(source)

    cleaned_path = _strip_pdf_from_path(source)
    try:
        with pikepdf.open(cleaned_path) as cleaned:
            raw_jpeg = cleaned.pages[0].images["/Im1"].read_raw_bytes()
            assert b"embedded-private-metadata" not in raw_jpeg
            with Image.open(BytesIO(raw_jpeg)) as decoded:
                decoded.load()
                assert decoded.size == (32, 32)
    finally:
        cleaned_path.unlink(missing_ok=True)
