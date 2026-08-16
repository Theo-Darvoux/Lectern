"""Dynamic Open Graph (1200x630) social share card generator using Pillow."""

from __future__ import annotations

import contextlib
import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

_ASSETS_FONTS_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "fonts"
_FONT_BOLD_PATH = _ASSETS_FONTS_DIR / "LiberationSans-Bold.ttf"
_FONT_REG_PATH = _ASSETS_FONTS_DIR / "LiberationSans-Regular.ttf"

_SYSTEM_FALLBACK_FONTS_BOLD = [
    "/usr/share/fonts/liberation-sans-fonts/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
]

_SYSTEM_FALLBACK_FONTS_REG = [
    "/usr/share/fonts/liberation-sans-fonts/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
]


def _get_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    primary_path = _FONT_BOLD_PATH if bold else _FONT_REG_PATH
    if primary_path.is_file():
        try:
            return ImageFont.truetype(str(primary_path), size)
        except Exception:
            pass

    fallbacks = _SYSTEM_FALLBACK_FONTS_BOLD if bold else _SYSTEM_FALLBACK_FONTS_REG
    for fb in fallbacks:
        if Path(fb).is_file():
            with contextlib.suppress(Exception):
                return ImageFont.truetype(fb, size)

    return ImageFont.load_default()


def hex_to_rgb(hex_str: str | None, default: tuple[int, int, int] = (124, 58, 237)) -> tuple[int, int, int]:
    """Parse a hex color string (#rgb, #rrggbb, #rrggbbaa) into an (R, G, B) tuple."""
    if not hex_str:
        return default
    try:
        clean = hex_str.strip().lstrip("#")
        if len(clean) == 3:
            return tuple(int(c * 2, 16) for c in clean)  # type: ignore[return-value]
        if len(clean) >= 6:
            return tuple(int(clean[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except Exception:
        pass
    return default


def wrap_text(
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
    max_lines: int = 2,
) -> list[str]:
    """Wrap text to fit within max_width for a given font, truncating with ellipsis if needed."""
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current_line: list[str] = []

    for word in words:
        test_line = " ".join(current_line + [word])
        try:
            bbox = font.getbbox(test_line)  # type: ignore[union-attr]
            w = bbox[2] - bbox[0]
        except Exception:
            w = len(test_line) * 10

        if w <= max_width:
            current_line.append(word)
        else:
            if current_line:
                lines.append(" ".join(current_line))
                current_line = [word]
            else:
                lines.append(word)
                current_line = []
            if len(lines) == max_lines:
                break

    if current_line and len(lines) < max_lines:
        lines.append(" ".join(current_line))

    if len(lines) == max_lines and words:
        total_words = sum(len(ln.split()) for ln in lines)
        if total_words < len(words):
            last_line = lines[-1]
            while last_line:
                test_str = last_line + "…"
                try:
                    bbox = font.getbbox(test_str)  # type: ignore[union-attr]
                    w = bbox[2] - bbox[0]
                except Exception:
                    w = len(test_str) * 10
                if w <= max_width:
                    lines[-1] = test_str
                    break
                parts = last_line.rsplit(" ", 1)
                last_line = parts[0] if len(parts) > 1 else last_line[:-1]

    return lines


@lru_cache(maxsize=128)
def generate_og_image(
    site_name: str = "INTellect",
    title: str = "INTellect",
    subtitle: str | None = "Plateforme collaborative de cours, annales et QCM",
    badge: str | None = "Plateforme Académique",
    theme_color_hex: str = "#8b5cf6",
    host: str | None = "intellect.clubcode.fr",
    footer_tags: str = "Cours & Annales|Partage Collaboratif|QCM & Quiz",
) -> bytes:
    """Generate a high-definition 1200x630 Open Graph preview image (PNG bytes)."""
    width, height = 1200, 630
    theme_rgb = hex_to_rgb(theme_color_hex)

    # 1. Base dark background (Sleek deep navy-slate #080c14)
    img = Image.new("RGBA", (width, height), (8, 12, 20, 255))

    # 2. Ambient glows with smooth Gaussian Blur
    glow_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    s_w, s_h = width // 2, height // 2

    # Glow 1: Top right primary glow
    g1 = Image.new("RGBA", (s_w, s_h), (0, 0, 0, 0))
    ImageDraw.Draw(g1).ellipse((s_w - 260, -80, s_w + 100, 280), fill=(*theme_rgb, 100))
    g1 = g1.filter(ImageFilter.GaussianBlur(radius=50))
    glow_layer.alpha_composite(g1.resize((width, height), Image.Resampling.BICUBIC))

    # Glow 2: Bottom left subtle secondary glow
    g2 = Image.new("RGBA", (s_w, s_h), (0, 0, 0, 0))
    ImageDraw.Draw(g2).ellipse((-100, s_h - 180, 220, s_h + 100), fill=(*theme_rgb, 50))
    g2 = g2.filter(ImageFilter.GaussianBlur(radius=45))
    glow_layer.alpha_composite(g2.resize((width, height), Image.Resampling.BICUBIC))

    img = Image.alpha_composite(img, glow_layer)

    # 3. Card surface (Glassmorphism container with crisp vector styling)
    card_margin_x, card_margin_y = 48, 44
    card_w, card_h = width - (card_margin_x * 2), height - (card_margin_y * 2)
    card_box = (card_margin_x, card_margin_y, card_margin_x + card_w, card_margin_y + card_h)

    card_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    card_draw = ImageDraw.Draw(card_layer)

    # Card surface fill (dark glass #0f172a with 88% opacity) and 1px border
    card_draw.rounded_rectangle(
        card_box,
        radius=24,
        fill=(15, 23, 42, 225),
        outline=(255, 255, 255, 30),
        width=1,
    )

    # Subtle dot grid pattern
    for dot_x in range(card_margin_x + 36, card_margin_x + card_w - 36, 32):
        for dot_y in range(card_margin_y + 36, card_margin_y + card_h - 36, 32):
            card_draw.ellipse((dot_x, dot_y, dot_x + 1.5, dot_y + 1.5), fill=(255, 255, 255, 10))

    # Glowing top accent bar
    card_draw.rounded_rectangle(
        (card_margin_x + 40, card_margin_y, card_margin_x + card_w - 40, card_margin_y + 3),
        radius=2,
        fill=(*theme_rgb, 230),
    )

    img = Image.alpha_composite(img, card_layer)

    # 4. Separate UI elements layer for crisp alpha compositing
    ui_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ui_draw = ImageDraw.Draw(ui_layer)

    font_brand = _get_font(18, bold=True)
    font_badge = _get_font(14, bold=True)
    font_footer = _get_font(15, bold=False)
    font_footer_bold = _get_font(15, bold=True)

    # Header: Left Brand Pill
    pill_y = card_margin_y + 34
    brand_text = site_name.upper()
    try:
        b_bbox = font_brand.getbbox(brand_text)  # type: ignore[union-attr]
        b_w = int(b_bbox[2] - b_bbox[0])
    except Exception:
        b_w = len(brand_text) * 12
    brand_pill_w = b_w + 48
    brand_pill_x = card_margin_x + 40

    ui_draw.rounded_rectangle(
        (brand_pill_x, pill_y, brand_pill_x + brand_pill_w, pill_y + 36),
        radius=18,
        fill=(30, 41, 59, 230),
        outline=(255, 255, 255, 45),
        width=1,
    )
    # Glowing status dot
    ui_draw.ellipse((brand_pill_x + 14, pill_y + 13, brand_pill_x + 24, pill_y + 23), fill=(*theme_rgb, 255))
    ui_draw.text((brand_pill_x + 32, pill_y + 8), brand_text, font=font_brand, fill=(255, 255, 255, 255))

    # Header: Right Category Badge
    if badge:
        badge_text = badge.upper()
        try:
            bd_bbox = font_badge.getbbox(badge_text)  # type: ignore[union-attr]
            bd_w = int(bd_bbox[2] - bd_bbox[0])
        except Exception:
            bd_w = len(badge_text) * 10
        badge_pill_w = bd_w + 32
        badge_pill_x = card_margin_x + card_w - 40 - badge_pill_w

        ui_draw.rounded_rectangle(
            (badge_pill_x, pill_y, badge_pill_x + badge_pill_w, pill_y + 36),
            radius=18,
            fill=(*theme_rgb, 50),
            outline=(*theme_rgb, 140),
            width=1,
        )
        ui_draw.text((badge_pill_x + 16, pill_y + 10), badge_text, font=font_badge, fill=(255, 255, 255, 255))

    # Main Content Area
    content_x = card_margin_x + 45
    content_max_w = card_w - 90

    # Determine Title Font Size dynamically
    title_clean = title.strip()
    if len(title_clean) > 65:
        font_title_size = 42
        line_spacing = 52
    elif len(title_clean) > 35:
        font_title_size = 50
        line_spacing = 60
    else:
        font_title_size = 58
        line_spacing = 70

    font_title = _get_font(font_title_size, bold=True)
    title_lines = wrap_text(title_clean, font_title, content_max_w, max_lines=2)

    # Subtitle
    font_sub = _get_font(24, bold=False)
    sub_lines: list[str] = []
    if subtitle and subtitle.strip():
        sub_lines = wrap_text(subtitle.strip(), font_sub, content_max_w, max_lines=2)

    total_text_h = (len(title_lines) * line_spacing) + (len(sub_lines) * 34) + (20 if sub_lines else 0)
    avail_h = card_h - 180
    start_y = card_margin_y + 110 + max(0, (avail_h - total_text_h) // 2)

    # Draw Title Lines
    curr_y = start_y
    for line in title_lines:
        ui_draw.text((content_x, curr_y), line, font=font_title, fill=(248, 250, 252, 255))
        curr_y += line_spacing

    if sub_lines:
        curr_y += 14
        for line in sub_lines:
            ui_draw.text((content_x, curr_y), line, font=font_sub, fill=(148, 163, 184, 255))
            curr_y += 34

    # Footer Area
    footer_y = card_margin_y + card_h - 58

    # Feature Badges on Left
    feature_pills = [p.strip() for p in footer_tags.split("|") if p.strip()]
    cur_fx = content_x
    for feat in feature_pills:
        try:
            ft_bbox = font_footer.getbbox(feat)  # type: ignore[union-attr]
            ft_w = int(ft_bbox[2] - ft_bbox[0])
        except Exception:
            ft_w = len(feat) * 9
        p_w = ft_w + 34
        ui_draw.rounded_rectangle(
            (cur_fx, footer_y, cur_fx + p_w, footer_y + 30),
            radius=8,
            fill=(30, 41, 59, 180),
            outline=(255, 255, 255, 25),
            width=1,
        )
        # Small accent dot inside pill
        ui_draw.ellipse((cur_fx + 10, footer_y + 11, cur_fx + 18, footer_y + 19), fill=(*theme_rgb, 200))
        ui_draw.text((cur_fx + 24, footer_y + 6), feat, font=font_footer, fill=(203, 213, 225, 255))
        cur_fx += p_w + 12

    # Host on Right
    if host:
        h_clean = host.split(":")[0]
        try:
            h_bbox = font_footer_bold.getbbox(h_clean)  # type: ignore[union-attr]
            h_w = int(h_bbox[2] - h_bbox[0])
        except Exception:
            h_w = len(h_clean) * 9
        hx = int(card_margin_x + card_w - 45 - h_w)
        ui_draw.text((hx, footer_y + 6), h_clean, font=font_footer_bold, fill=(148, 163, 184, 255))

    img = Image.alpha_composite(img, ui_layer)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
