"""Tests for the web app manifest and home-screen icons (/api/app/*)."""

from __future__ import annotations

import io
from unittest.mock import patch

from httpx import AsyncClient
from PIL import Image

from app.core.media.app_icon import (
    generate_app_icon,
    monogram_letter,
    render_icon_file,
    resolve_icon_file,
)


async def test_manifest_reflects_runtime_branding(client: AsyncClient) -> None:
    from app.config import settings

    with (
        patch.object(settings, "site_name", "INTellect"),
        patch.object(settings, "og_site_name", None),
        patch.object(settings, "og_theme_color", None),
        patch.object(settings, "primary_color", "#8b5cf6"),
    ):
        resp = await client.get("/api/app/manifest.webmanifest")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/manifest+json")
    manifest = resp.json()
    assert manifest["name"] == "INTellect"
    assert manifest["short_name"] == "INTellect"
    assert manifest["theme_color"] == "#8b5cf6"
    assert manifest["start_url"] == "/"
    assert manifest["scope"] == "/"
    assert manifest["display"] == "standalone"
    sizes = {icon["sizes"] for icon in manifest["icons"]}
    assert {"192x192", "512x512"} <= sizes
    assert any(icon.get("purpose") == "maskable" for icon in manifest["icons"])


async def test_manifest_short_name_is_truncated(client: AsyncClient) -> None:
    from app.config import settings

    with (
        patch.object(settings, "site_name", "A very long platform name"),
        patch.object(settings, "og_site_name", None),
    ):
        resp = await client.get("/api/app/manifest.webmanifest")

    assert len(resp.json()["short_name"]) <= 12


async def test_icons_are_square_pngs(client: AsyncClient) -> None:
    for path, size in (
        ("/api/app/icon-180.png", 180),
        ("/api/app/icon-192.png", 192),
        ("/api/app/icon-512.png", 512),
        ("/api/app/icon-maskable-512.png", 512),
    ):
        resp = await client.get(path)
        assert resp.status_code == 200, path
        assert resp.headers["content-type"] == "image/png"
        image = Image.open(io.BytesIO(resp.content))
        assert image.size == (size, size)
        # iOS turns transparent apple-touch-icon pixels black.
        assert image.mode == "RGB"


async def test_unsupported_icon_size_is_rejected(client: AsyncClient) -> None:
    resp = await client.get("/api/app/icon-64.png")
    assert resp.status_code == 404


def test_monogram_letter() -> None:
    assert monogram_letter("INTellect") == "I"
    assert monogram_letter("  wiki") == "W"
    assert monogram_letter("--") == "?"


async def test_default_icon_is_the_bundled_logo(client: AsyncClient) -> None:
    from app.config import settings

    bundled = resolve_icon_file(settings.app_icon_path)
    assert bundled is not None
    resp = await client.get("/api/app/icon-192.png")
    assert resp.content == render_icon_file(bundled, 192)
    # The bundled artwork ships a dedicated maskable variant.
    resp = await client.get("/api/app/icon-maskable-512.png")
    assert resp.content == render_icon_file(bundled, 512, maskable=True)
    assert resp.content != render_icon_file(bundled, 512)


async def test_empty_icon_path_falls_back_to_monogram(client: AsyncClient) -> None:
    from app.config import settings

    with (
        patch.object(settings, "app_icon_path", ""),
        patch.object(settings, "site_name", "INTellect"),
        patch.object(settings, "og_site_name", None),
        patch.object(settings, "og_theme_color", None),
        patch.object(settings, "primary_color", "#8b5cf6"),
    ):
        resp = await client.get("/api/app/icon-192.png")

    assert resp.content == generate_app_icon("I", "#8b5cf6", 192)


def test_missing_icon_file_falls_back_to_monogram(tmp_path) -> None:
    assert resolve_icon_file(str(tmp_path / "missing.png")) is None
    assert resolve_icon_file(None) is None


def test_icon_file_without_maskable_variant_is_padded(tmp_path) -> None:
    # Red border on a blue icon: the derived maskable icon must shrink the art and
    # fill the margin with the corner color.
    source = Image.new("RGBA", (300, 200), (255, 0, 0, 255))
    source.paste((0, 0, 255, 255), (60, 10, 240, 190))
    path = tmp_path / "icon.png"
    source.save(path)

    plain = Image.open(io.BytesIO(render_icon_file(path, 100)))
    maskable = Image.open(io.BytesIO(render_icon_file(path, 100, maskable=True)))

    # Non-square art is center-cropped to a square, then made opaque.
    assert plain.size == maskable.size == (100, 100)
    assert plain.mode == maskable.mode == "RGB"
    assert plain.getpixel((50, 50)) == (0, 0, 255)
    assert maskable.getpixel((50, 50)) == (0, 0, 255)
    assert maskable.getpixel((2, 50)) == (255, 0, 0)
