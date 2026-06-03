"""Server-rendered Open Graph / link-preview metadata.

The web frontend is a static export, so its HTML <head> is baked at build time
and cannot reflect the runtime admin branding config. Social/link-preview
crawlers (Slack, Discord, WhatsApp, Facebook, etc.) don't run JS, so they never
see the branding the client applies after load. The top-level nginx routes those
crawler User-Agents here; humans keep getting the static SPA untouched.
"""

import html
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.database import get_db
from app.services.directory import resolve_browse_path
from app.services.material import get_material_by_id
from app.services.user import get_user_by_id

router = APIRouter(prefix="/api", tags=["og"])


async def _resolve_resource(db: AsyncSession, path: str) -> tuple[str, str | None] | None:
    """Best-effort per-resource (title, description) for a known route.

    Resolved as an anonymous viewer would see it, so private resources simply
    fall through to site branding. Any failure returns None (fall back).
    """
    segments = [s for s in path.strip("/").split("/") if s]
    if not segments:
        return None

    try:
        if segments[0] == "browse":
            sub = "/".join(segments[1:])
            if not sub:
                return None
            result = await resolve_browse_path(db, sub, current_user_id=None)
            if result.get("type") == "material":
                material = result.get("material") or {}
                title = material.get("title")
                if title:
                    return title, material.get("description")
            elif result.get("type") == "directory_listing":
                directory = result.get("directory") or {}
                name = directory.get("name")
                if name:
                    return name, directory.get("description")
            return None

        if segments[0] == "profile" and len(segments) >= 2:
            user = await get_user_by_id(db, segments[1])
            if user is not None and user.display_name:
                return user.display_name, user.bio

        if segments[0] == "qcm" and len(segments) >= 2 and segments[1] not in {"new", "preview"}:
            material = await get_material_by_id(db, segments[1])
            if material is not None and material.title:
                return material.title, material.description
    except Exception:
        return None

    return None


def _meta(prop: str, content: str, *, name: bool = False) -> str:
    attr = "name" if name else "property"
    return f'<meta {attr}="{html.escape(prop, quote=True)}" content="{html.escape(content, quote=True)}">'


@router.get("/og", response_class=HTMLResponse)
async def render_og(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> HTMLResponse:
    site_name = settings.site_name or "WikINT"
    site_description = settings.site_description or ""
    og_image = settings.og_image_url
    favicon = settings.site_favicon_url

    original_path = request.headers.get("x-original-path", "/")
    proto = request.headers.get("x-forwarded-proto", "https")
    host = request.headers.get("host", "")
    canonical = f"{proto}://{host}{original_path}" if host else original_path

    resource = await _resolve_resource(db, original_path)
    if resource is not None:
        resource_title, resource_description = resource
        title = f"{resource_title} • {site_name}"
        description = resource_description or site_description
        og_title = resource_title
    else:
        title = site_name
        description = site_description
        og_title = site_name

    tags = [
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{html.escape(title)}</title>",
        _meta("description", description, name=True),
        _meta("og:type", "website"),
        _meta("og:site_name", site_name),
        _meta("og:title", og_title),
        _meta("og:description", description),
        _meta("og:url", canonical),
        _meta("twitter:card", "summary_large_image", name=True),
        _meta("twitter:title", og_title, name=True),
        _meta("twitter:description", description, name=True),
        f'<link rel="canonical" href="{html.escape(canonical, quote=True)}">',
    ]
    if og_image:
        tags.append(_meta("og:image", og_image))
        tags.append(_meta("twitter:image", og_image, name=True))
    if favicon:
        tags.append(f'<link rel="icon" href="{html.escape(favicon, quote=True)}">')

    head = "\n    ".join(tags)
    body = f'<p><a href="{html.escape(canonical, quote=True)}">{html.escape(title)}</a></p>'
    document = (
        "<!doctype html>\n"
        '<html lang="fr">\n'
        f"  <head>\n    {head}\n"
        f'    <meta http-equiv="refresh" content="0; url={html.escape(canonical, quote=True)}">\n'
        "  </head>\n"
        f"  <body>\n    {body}\n  </body>\n"
        "</html>\n"
    )
    return HTMLResponse(content=document, headers={"Cache-Control": "public, max-age=60"})
