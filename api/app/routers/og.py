"""Server-rendered Open Graph / link-preview metadata & dynamic banner images.

The web frontend is a static export, so its HTML <head> is baked at build time
and cannot reflect the runtime admin branding config. Social/link-preview
crawlers (Slack, Discord, WhatsApp, Facebook, etc.) don't run JS, so they never
see the branding the client applies after load. The top-level nginx routes those
crawler User-Agents here; humans keep getting the static SPA untouched.
"""

from __future__ import annotations

import html
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.database.database import get_db
from app.core.media.og_image import generate_og_image
from app.services.directory import resolve_browse_path
from app.services.material import get_material_by_id
from app.services.user import get_user_by_id

router = APIRouter(prefix="/api", tags=["og"])


async def _resolve_resource(db: AsyncSession, path: str) -> tuple[str, str | None, str, str] | None:
    """Best-effort per-resource (title, description, badge, footer_tags) for a known route.

    Resolved as an anonymous viewer would see it, so private resources simply
    fall through to site branding. Any failure returns None (fall back).
    """
    segments = [s for s in path.strip("/").split("/") if s]
    if not segments:
        return None

    try:
        root_seg = segments[0].lower()

        if root_seg == "browse":
            sub = "/".join(segments[1:])
            if not sub:
                return None
            result = await resolve_browse_path(db, sub, current_user_id=None)
            if result.get("type") == "material":
                material = result.get("material") or {}
                title = material.get("title")
                if title:
                    return (
                        title,
                        material.get("description"),
                        "Document",
                        "Document|Téléchargement|Partage",
                    )
            elif result.get("type") == "directory_listing":
                directory = result.get("directory") or {}
                name = directory.get("name")
                if name:
                    return name, directory.get("description"), "Dossier", "Cours|Annales|Exercices"
            return None

        if root_seg == "profile" and len(segments) >= 2:
            user = await get_user_by_id(db, segments[1])
            if user is not None and user.display_name:
                return user.display_name, user.bio, "Membre", "Profil|Contributeur|Communauté"

        if root_seg == "qcm" and len(segments) >= 2 and segments[1] not in {"new", "preview"}:
            material = await get_material_by_id(db, segments[1])
            if material is not None and material.title:
                return (
                    material.title,
                    material.description,
                    "QCM & Quiz",
                    "QCM Interactif|Auto-évaluation|Révisions",
                )
    except Exception:
        return None

    return None


def _meta(prop: str, content: str, *, name: bool = False) -> str:
    attr = "name" if name else "property"
    return f'<meta {attr}="{html.escape(prop, quote=True)}" content="{html.escape(content, quote=True)}">'


@router.get("/og/image", response_class=Response)
async def render_og_image(
    request: Request,
    title: Annotated[str | None, Query(description="Main title to display")] = None,
    subtitle: Annotated[str | None, Query(description="Subtitle/description")] = None,
    badge: Annotated[str | None, Query(description="Category or section badge")] = None,
    theme: Annotated[str | None, Query(description="Theme color hex")] = None,
    tags: Annotated[str | None, Query(description="Pipe-separated footer tags")] = None,
) -> Response:
    """Generate a dynamic 1200x630 Open Graph preview image (PNG)."""
    site_name = settings.og_site_name or settings.site_name or "Lectern"
    final_title = title or settings.og_title or site_name
    final_subtitle = (
        subtitle
        or settings.og_description
        or settings.site_description
        or settings.og_tagline
        or "Plateforme collaborative de cours & annales"
    )
    final_badge = badge or settings.og_tagline or "Plateforme Académique"
    final_theme = theme or settings.og_theme_color or settings.primary_color or "#6366f1"
    final_tags = tags or "Cours & Annales|Partage Collaboratif|QCM & Quiz"
    host = request.headers.get("host", "")

    image_bytes = generate_og_image(
        site_name=site_name,
        title=final_title,
        subtitle=final_subtitle,
        badge=final_badge,
        theme_color_hex=final_theme,
        host=host,
        footer_tags=final_tags,
    )

    return Response(
        content=image_bytes,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=86400, s-maxage=86400, stale-while-revalidate=604800",
        },
    )


@router.get("/og", response_class=HTMLResponse)
async def render_og(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> HTMLResponse:
    site_name = settings.og_site_name or settings.site_name or "Lectern"
    site_description = settings.og_description or settings.site_description or ""
    configured_og_image = settings.og_image_url
    favicon = settings.site_favicon_url
    theme_color = settings.og_theme_color or settings.primary_color or "#6366f1"
    locale = settings.og_locale or "fr_FR"

    original_path = request.headers.get("x-original-path", "/")
    proto = request.headers.get("x-forwarded-proto", "https")
    host = request.headers.get("host", "")
    origin = f"{proto}://{host}" if host else ""
    canonical = f"{origin}{original_path}" if origin else original_path

    resource = await _resolve_resource(db, original_path)
    if resource is not None:
        resource_title, resource_description, resource_badge, resource_tags = resource
        title = f"{resource_title} • {site_name}"
        description = resource_description or site_description
        og_title = resource_title
        badge = resource_badge
        footer_tags = resource_tags
        og_type = "article" if resource_badge in {"Document", "QCM & Quiz"} else "website"
    else:
        title = settings.og_title or site_name
        description = site_description
        og_title = settings.og_title or site_name
        badge = settings.og_tagline or "Plateforme Académique"
        footer_tags = "Cours & Annales|Partage Collaboratif|QCM & Quiz"
        og_type = "website"

    # Determine OG Image URL: use configured static URL if present, otherwise dynamic generator
    if configured_og_image:
        if configured_og_image.startswith(("http://", "https://")):
            og_image = configured_og_image
        elif origin and configured_og_image.startswith("/"):
            og_image = f"{origin}{configured_og_image}"
        else:
            og_image = configured_og_image
    else:
        # Generate dynamic image URL
        query_params = [
            f"title={quote(og_title)}",
            f"subtitle={quote(description[:140])}",
            f"badge={quote(badge)}",
            f"theme={quote(theme_color)}",
            f"tags={quote(footer_tags)}",
        ]
        img_path = f"/api/og/image?{'&'.join(query_params)}"
        og_image = f"{origin}{img_path}" if origin else img_path

    tags = [
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{html.escape(title)}</title>",
        _meta("theme-color", theme_color, name=True),
        _meta("msapplication-TileColor", theme_color, name=True),
        _meta("description", description, name=True),
        _meta("og:type", og_type),
        _meta("og:site_name", site_name),
        _meta("og:title", og_title),
        _meta("og:description", description),
        _meta("og:url", canonical),
        _meta("og:locale", locale),
        _meta("og:image", og_image),
        _meta("og:image:type", "image/png"),
        _meta("og:image:width", "1200"),
        _meta("og:image:height", "630"),
        _meta("og:image:alt", og_title),
        _meta("twitter:card", "summary_large_image", name=True),
        _meta("twitter:title", og_title, name=True),
        _meta("twitter:description", description, name=True),
        _meta("twitter:image", og_image, name=True),
        _meta("twitter:image:alt", og_title, name=True),
        f'<link rel="canonical" href="{html.escape(canonical, quote=True)}">',
    ]

    if proto == "https" and og_image.startswith("https://"):
        tags.insert(13, _meta("og:image:secure_url", og_image))

    if settings.og_twitter_site:
        tags.append(_meta("twitter:site", settings.og_twitter_site, name=True))
    if settings.og_twitter_creator:
        tags.append(_meta("twitter:creator", settings.og_twitter_creator, name=True))

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
