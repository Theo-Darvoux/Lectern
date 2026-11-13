"""Home-screen (PWA / apple-touch) icons using Pillow.

The icon comes from a square image file (``APP_ICON_PATH``; the bundled
INTellect logo by default), resized per request size. With no usable file it
falls back to a monogram: the first letter of the site name, centered on the
brand color. Both read the runtime settings, so a rebrand only needs an env
change, not a web rebuild.
"""

from __future__ import annotations

import io
import logging
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw

from app.core.media.og_image import _get_font, hex_to_rgb

logger = logging.getLogger(__name__)

# Relative APP_ICON_PATH values resolve against the API root (the folder holding
# ``app/``), so the default works both in the container and in a local checkout.
_API_ROOT = Path(__file__).resolve().parents[3]

# A maskable icon may be cropped to a circle of 80% diameter by Android. An icon
# file without a dedicated maskable variant is shrunk to this share of the canvas.
_MASKABLE_FILE_SCALE = 0.8

# Share of the icon height taken by the letter. A maskable icon may be cropped
# to a circle by Android, so its glyph must stay inside the central safe zone
# (a circle of 80% diameter); a plain icon can use more of the canvas.
_GLYPH_SCALE = 0.56
_GLYPH_SCALE_MASKABLE = 0.42


def monogram_letter(site_name: str) -> str:
    """First letter or digit of the site name, uppercased; ``"?"`` if there is none."""
    for char in site_name:
        if char.isalnum():
            return char.upper()
    return "?"


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


@lru_cache(maxsize=32)
def generate_app_icon(letter: str, color_hex: str, size: int, *, maskable: bool = False) -> bytes:
    """Render a square, full-bleed PNG icon of ``size`` pixels.

    The background is opaque because iOS fills transparent apple-touch-icon
    pixels with black, and launchers apply their own corner mask.
    """
    background = hex_to_rgb(color_hex, default=(59, 130, 246))
    # Light brand colors get a dark letter so the icon stays readable.
    foreground = (17, 24, 39) if _relative_luminance(background) > 0.5 else (255, 255, 255)

    image = Image.new("RGB", (size, size), background)
    draw = ImageDraw.Draw(image)

    scale = _GLYPH_SCALE_MASKABLE if maskable else _GLYPH_SCALE
    font = _get_font(round(size * scale / 0.72), bold=True)
    # Anchor "mm" centers the glyph box on the point, independent of the
    # font's ascender/descender metrics.
    draw.text((size / 2, size / 2), letter, font=font, fill=foreground, anchor="mm")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def resolve_icon_file(value: str | None) -> Path | None:
    """The configured icon file, or ``None`` (monogram) if unset or missing."""
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = _API_ROOT / path
    if not path.is_file():
        logger.warning("APP_ICON_PATH %s is not a file; using the monogram icon", path)
        return None
    return path


def _maskable_variant(path: Path) -> Path | None:
    """``<name>-maskable<ext>`` next to the icon, if the artwork ships one."""
    candidate = path.with_name(f"{path.stem}-maskable{path.suffix}")
    return candidate if candidate.is_file() else None


def render_icon_file(path: Path, size: int, *, maskable: bool = False) -> bytes:
    """Render the icon file as an opaque square PNG of ``size`` pixels."""
    if maskable:
        variant = _maskable_variant(path)
        if variant is not None:
            return _render_icon_file(str(variant), variant.stat().st_mtime, size, False)
    # The modification time is part of the cache key, so replacing the file
    # takes effect without a restart.
    return _render_icon_file(str(path), path.stat().st_mtime, size, maskable)


@lru_cache(maxsize=32)
def _render_icon_file(path: str, mtime: float, size: int, shrink: bool) -> bytes:
    with Image.open(path) as source:
        art = source.convert("RGBA")
    # Square it, then flatten on the top-left pixel's color: launchers want an
    # opaque, full-bleed image and apply their own corner mask.
    side = min(art.size)
    left, top = (art.width - side) // 2, (art.height - side) // 2
    art = art.crop((left, top, left + side, top + side))
    corner = art.getpixel((0, 0))
    background = tuple(corner[:3]) if isinstance(corner, tuple) else (0, 0, 0)
    canvas = Image.new("RGB", (size, size), background)

    inner = round(size * _MASKABLE_FILE_SCALE) if shrink else size
    art = art.resize((inner, inner), Image.Resampling.LANCZOS)
    offset = (size - inner) // 2
    canvas.paste(art, (offset, offset), art)

    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
