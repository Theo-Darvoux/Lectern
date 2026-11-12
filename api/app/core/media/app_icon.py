"""Home-screen (PWA / apple-touch) icon generator using Pillow.

Instances have no bundled logo, so the icon is a monogram: the first letter of
the site name, centered on the brand color. It is rendered from the runtime
branding settings, so a rebrand only needs an env change, not a web rebuild.
"""

from __future__ import annotations

import io
from functools import lru_cache

from PIL import Image, ImageDraw

from app.core.media.og_image import _get_font, hex_to_rgb

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
