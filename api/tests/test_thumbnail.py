import tempfile
from pathlib import Path

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
