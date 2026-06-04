import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image, ImageDraw

from app.workers.upload.stages.thumbnail import _is_blank_thumbnail


def _save(img: Image.Image, suffix: str = ".png") -> Path:
    path = Path(tempfile.mkdtemp()) / f"thumb{suffix}"
    img.save(path)
    return path


def test_all_white_is_blank() -> None:
    """A uniform white image has nothing to show → treated as blank."""
    img = Image.new("RGB", (200, 120), "white")
    assert _is_blank_thumbnail(_save(img)) is True


def test_white_page_with_text_is_not_blank() -> None:
    """A white page with dark content (text/figures) must NOT be discarded —
    this is the regression for PDFs whose thumbnail was wrongly dropped."""
    img = Image.new("RGB", (200, 120), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle([10, 10, 120, 60], fill="black")
    draw.text((10, 80), "Cours 2025", fill="black")
    assert _is_blank_thumbnail(_save(img)) is False


def test_pale_page_with_title_bar_is_not_blank() -> None:
    """A bright/pale page that still has a coloured title bar has real contrast
    and should be kept."""
    img = Image.new("RGB", (200, 120), (245, 245, 245))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 200, 25], fill=(40, 60, 120))
    assert _is_blank_thumbnail(_save(img)) is False


@pytest.mark.asyncio
async def test_run_thumbnail_stage_svg() -> None:
    """SVG files should be rendered to a WebP thumbnail via rsvg-convert."""
    from app.core.processing import ProcessingFile
    from app.workers.upload.stages.thumbnail import run_thumbnail_stage

    svg_content = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">'
        '<rect width="200" height="100" fill="#4a90d9"/>'
        '<text x="10" y="60" font-size="24" fill="white">WikINT</text>'
        "</svg>"
    )
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False, mode="w") as f:
        f.write(svg_content)
        temp_path = Path(f.name)

    pf = ProcessingFile(temp_path, temp_path.stat().st_size)
    thumb_path_str = None
    try:
        thumb_path_str = await run_thumbnail_stage(pf, "image/svg+xml", "diagram.svg")
        assert thumb_path_str is not None
        thumb_path = Path(thumb_path_str)
        assert thumb_path.exists()
        assert thumb_path.suffix == ".webp"
        with Image.open(thumb_path) as img:
            assert img.format == "WEBP"
    finally:
        pf.cleanup()
        if thumb_path_str:
            Path(thumb_path_str).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_run_thumbnail_stage_markdown() -> None:
    """Markdown files should be successfully converted to a WebP thumbnail."""
    from app.core.processing import ProcessingFile
    from app.workers.upload.stages.thumbnail import run_thumbnail_stage

    # Create a temp markdown file
    with tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w") as f:
        f.write(
            "# Hello World\n\nThis is a sample markdown document to test thumbnail generation.\n\n- Bullet point 1\n- Bullet point 2\n"
        )
        temp_path = Path(f.name)

    pf = ProcessingFile(temp_path, temp_path.stat().st_size)
    thumb_path_str = None
    try:
        thumb_path_str = await run_thumbnail_stage(pf, "text/markdown", "test.md")
        assert thumb_path_str is not None
        thumb_path = Path(thumb_path_str)
        assert thumb_path.exists()
        assert thumb_path.suffix == ".webp"

        # Verify it's a valid image and not blank
        with Image.open(thumb_path) as img:
            assert img.format == "WEBP"

        assert _is_blank_thumbnail(thumb_path) is False
    finally:
        pf.cleanup()
        if thumb_path_str:
            Path(thumb_path_str).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_run_thumbnail_stage_text() -> None:
    """Text/code files should be successfully converted to a WebP thumbnail."""
    from app.core.processing import ProcessingFile
    from app.workers.upload.stages.thumbnail import run_thumbnail_stage

    # Create a temp latex file
    with tempfile.NamedTemporaryFile(suffix=".tex", delete=False, mode="w") as f:
        f.write(
            "\\documentclass{article}\n\\begin{document}\nHello World from LaTeX!\n\\end{document}\n"
        )
        temp_path = Path(f.name)

    pf = ProcessingFile(temp_path, temp_path.stat().st_size)
    thumb_path_str = None
    try:
        thumb_path_str = await run_thumbnail_stage(pf, "text/x-tex", "test.tex")
        assert thumb_path_str is not None
        thumb_path = Path(thumb_path_str)
        assert thumb_path.exists()
        assert thumb_path.suffix == ".webp"

        # Verify it's a valid image and not blank
        with Image.open(thumb_path) as img:
            assert img.format == "WEBP"

        assert _is_blank_thumbnail(thumb_path) is False
    finally:
        pf.cleanup()
        if thumb_path_str:
            Path(thumb_path_str).unlink(missing_ok=True)


# ── New behaviour: raise on failure, None only for unsupported types ──────────


@pytest.mark.asyncio
async def test_run_thumbnail_stage_unsupported_mime_returns_none() -> None:
    """Unsupported MIME types return None without raising — no retry needed."""
    from app.core.processing import ProcessingFile
    from app.workers.upload.stages.thumbnail import run_thumbnail_stage

    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        f.write(b"\x00" * 64)
        temp_path = Path(f.name)

    pf = ProcessingFile(temp_path, 64)
    try:
        result = await run_thumbnail_stage(pf, "application/octet-stream", "file.bin")
        assert result is None, "Unsupported MIME type must return None, not raise"
    finally:
        pf.cleanup()


@pytest.mark.asyncio
async def test_run_thumbnail_stage_raises_on_generator_failure() -> None:
    """A failing generator now raises instead of silently returning None."""
    from app.core.processing import ProcessingFile
    from app.workers.upload.stages.thumbnail import run_thumbnail_stage

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(b"\xff\xd8\xff" + b"\x00" * 64)
        temp_path = Path(f.name)

    pf = ProcessingFile(temp_path, temp_path.stat().st_size)
    try:
        with patch(
            "app.workers.upload.stages.thumbnail._thumbnail_image",
            new_callable=AsyncMock,
            side_effect=RuntimeError("simulated Pillow failure"),
        ):
            with pytest.raises(RuntimeError, match="simulated Pillow failure"):
                await run_thumbnail_stage(pf, "image/jpeg", "photo.jpg")
    finally:
        pf.cleanup()


@pytest.mark.asyncio
async def test_run_thumbnail_stage_cleans_up_partial_file_on_failure() -> None:
    """The partial thumb file is deleted even when the generator raises mid-write."""
    from app.core.processing import ProcessingFile
    from app.workers.upload.stages.thumbnail import run_thumbnail_stage

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(b"\xff\xd8\xff" + b"\x00" * 64)
        temp_path = Path(f.name)

    pf = ProcessingFile(temp_path, temp_path.stat().st_size)
    expected_thumb = pf.path.parent / f"thumb_{pf.path.name}.webp"

    async def _write_then_fail(input_path, output_path, size, quality):
        output_path.write_bytes(b"partial")
        raise OSError("disk full")

    try:
        with patch(
            "app.workers.upload.stages.thumbnail._thumbnail_image",
            side_effect=_write_then_fail,
        ):
            with pytest.raises(OSError):
                await run_thumbnail_stage(pf, "image/jpeg", "photo.jpg")

        assert not expected_thumb.exists(), (
            "Partial thumb file must be deleted after a generator failure"
        )
    finally:
        pf.cleanup()
        expected_thumb.unlink(missing_ok=True)
