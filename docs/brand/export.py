"""Rasterize the brand SVGs into the files the app serves.

- api/app/assets/brand/app-icon.png, app-icon-maskable.png: 512 px, opaque
  (iOS paints transparent apple-touch pixels black). The API resizes them.
- web/public/favicon.ico and web/src/app/favicon.ico: 16 to 64 px.

Usage (from the repository root, after generate.py):
    uv run --with resvg-py --with pillow python docs/brand/export.py
"""

import io
from pathlib import Path

import resvg_py
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
BRAND = ROOT / "docs" / "brand"


def render(name: str, width: int) -> Image.Image:
    png = resvg_py.svg_to_bytes(svg_path=str(BRAND / name), width=width)
    return Image.open(io.BytesIO(bytes(png)))


for name in ("app-icon", "app-icon-maskable"):
    out = ROOT / "api" / "app" / "assets" / "brand" / f"{name}.png"
    render(f"{name}.svg", 512).convert("RGB").save(out, optimize=True)
    print(out.relative_to(ROOT))

favicon = render("logo-int.svg", 256).convert("RGBA")
for out in (ROOT / "web" / "public" / "favicon.ico", ROOT / "web" / "src" / "app" / "favicon.ico"):
    favicon.save(out, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    print(out.relative_to(ROOT))
