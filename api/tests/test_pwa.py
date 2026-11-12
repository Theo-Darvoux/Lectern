"""Tests for the web app manifest and home-screen icons (/api/app/*)."""

from __future__ import annotations

import io
from unittest.mock import patch

from httpx import AsyncClient
from PIL import Image

from app.core.media.app_icon import monogram_letter


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
