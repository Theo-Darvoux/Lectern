"""Web app manifest and home-screen icons ("Add to Home Screen" / PWA install).

The web frontend is a static export, so anything baked into it at build time
cannot follow the runtime branding settings (released images are built without
``SITE_NAME``). The manifest and icons are therefore served here and read the
settings on every request.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse

from app.config import settings
from app.core.common.exceptions import NotFoundError
from app.core.media.app_icon import generate_app_icon, monogram_letter

router = APIRouter(prefix="/api/app", tags=["pwa"])

_CACHE_CONTROL = "public, max-age=3600, stale-while-revalidate=86400"

# 180 = apple-touch-icon; 192 and 512 are the sizes Chrome requires to install.
_ICON_SIZES = frozenset({180, 192, 512})


def _site_name() -> str:
    return settings.og_site_name or settings.site_name or "Lectern"


def _theme_color() -> str:
    return settings.og_theme_color or settings.primary_color or "#3b82f6"


def _short_name(name: str) -> str:
    # Launchers truncate labels at about 12 characters.
    return name if len(name) <= 12 else name[:12].rstrip()


@router.get("/manifest.webmanifest")
async def get_manifest() -> JSONResponse:
    name = _site_name()
    # Icon URLs are relative so they resolve next to the manifest, whatever
    # prefix the API is mounted under. start_url and scope are root-relative:
    # the app lives at the site root, not under /api.
    icons: list[dict[str, Any]] = [
        {"src": f"icon-{size}.png", "sizes": f"{size}x{size}", "type": "image/png"}
        for size in (192, 512)
    ]
    icons.append(
        {
            "src": "icon-maskable-512.png",
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "maskable",
        }
    )
    manifest = {
        "id": "/",
        "name": name,
        "short_name": _short_name(name),
        "description": settings.og_description or settings.site_description or "",
        "lang": (settings.og_locale or "fr_FR").split("_")[0],
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": _theme_color(),
        "icons": icons,
    }
    return JSONResponse(
        content=manifest,
        media_type="application/manifest+json",
        headers={"Cache-Control": _CACHE_CONTROL},
    )


def _icon_response(size: int, *, maskable: bool) -> Response:
    image = generate_app_icon(
        monogram_letter(_site_name()), _theme_color(), size, maskable=maskable
    )
    return Response(
        content=image, media_type="image/png", headers={"Cache-Control": _CACHE_CONTROL}
    )


@router.get("/icon-maskable-512.png", response_class=Response)
async def get_maskable_icon() -> Response:
    return _icon_response(512, maskable=True)


@router.get("/icon-{size}.png", response_class=Response)
async def get_icon(size: int) -> Response:
    if size not in _ICON_SIZES:
        raise NotFoundError("Icon size not available")
    return _icon_response(size, maskable=False)
