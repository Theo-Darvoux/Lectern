"""Open Graph metadata and preview banners.

The web app renders its HTML <head> on the server (Next.js ``generateMetadata``)
from ``GET /api/og/meta``, so every client, link-preview crawlers included, gets
the live branding and per-resource titles. ``GET /api/og/image`` draws the
matching 1200x630 banner.

Both endpoints take only a site path. The banner text is always resolved here,
never read from the query string, so nobody can mint a banner with arbitrary
text on this domain.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.database.database import get_db
from app.core.events.limiter import limiter
from app.core.media.og_image import generate_og_image
from app.schemas.og import OgMetaResponse
from app.services.directory import resolve_browse_path
from app.services.material import get_material_by_id
from app.services.user import get_user_by_id

router = APIRouter(prefix="/api", tags=["og"])

_DEFAULT_TAGS = "Cours & Annales|Partage Collaboratif|QCM & Quiz"
_DEFAULT_BADGE = "Plateforme Académique"
_DEFAULT_SUBTITLE = "Plateforme collaborative de cours & annales"
_ARTICLE_BADGES = frozenset({"Document", "QCM & Quiz"})
_BANNER_WIDTH = 1200
_BANNER_HEIGHT = 630

# A site path such as "/browse/maths/". Bounded so a lookup stays cheap.
SitePath = Annotated[str, Query(max_length=1024, pattern=r"^/")]


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


@dataclass(frozen=True)
class _Page:
    title: str
    og_title: str
    description: str
    badge: str
    tags: str
    type: Literal["website", "article"]
    is_resource: bool


def _site_name() -> str:
    return settings.og_site_name or settings.site_name or "Lectern"


def _theme_color() -> str:
    return settings.og_theme_color or settings.primary_color


def _origin() -> str:
    return settings.frontend_url.rstrip("/")


async def _resolve_page(db: AsyncSession, path: str) -> _Page:
    site_name = _site_name()
    site_description = settings.og_description or settings.site_description or ""
    resource = await _resolve_resource(db, path)
    if resource is not None:
        title, description, badge, tags = resource
        return _Page(
            title=f"{title} • {site_name}",
            og_title=title,
            description=description or site_description,
            badge=badge,
            tags=tags,
            type="article" if badge in _ARTICLE_BADGES else "website",
            is_resource=True,
        )
    site_title = settings.og_title or site_name
    return _Page(
        title=site_title,
        og_title=site_title,
        description=site_description,
        badge=settings.og_tagline or _DEFAULT_BADGE,
        tags=_DEFAULT_TAGS,
        type="website",
        is_resource=False,
    )


@router.get("/og/meta", response_model=OgMetaResponse)
async def get_og_meta(
    db: Annotated[AsyncSession, Depends(get_db)],
    response: Response,
    path: SitePath = "/",
) -> OgMetaResponse:
    """Metadata for one page of the web app, resolved as an anonymous visitor."""
    page = await _resolve_page(db, path)
    origin = _origin()

    configured = settings.og_image_url
    if configured:
        image = f"{origin}{configured}" if configured.startswith("/") else configured
        width = height = None
    else:
        # Site-level pages share one banner URL, so caches hold a single copy.
        query = f"?path={quote(path)}" if page.is_resource else ""
        image = f"{origin}/api/og/image{query}"
        width, height = _BANNER_WIDTH, _BANNER_HEIGHT

    response.headers["Cache-Control"] = "public, max-age=60"
    return OgMetaResponse(
        site_name=_site_name(),
        title=page.title,
        og_title=page.og_title,
        description=page.description,
        type=page.type,
        locale=settings.og_locale or "fr_FR",
        url=f"{origin}{path}",
        image=image,
        image_width=width,
        image_height=height,
        theme_color=_theme_color(),
        twitter_site=settings.og_twitter_site,
        twitter_creator=settings.og_twitter_creator,
    )


@router.get("/og/image", response_class=Response)
@limiter.limit("30/minute")
async def render_og_image(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    path: SitePath = "/",
) -> Response:
    """The 1200x630 preview banner (PNG) of one page of the web app."""
    page = await _resolve_page(db, path)
    image_bytes = generate_og_image(
        site_name=_site_name(),
        title=page.og_title,
        subtitle=(page.description or settings.og_tagline or _DEFAULT_SUBTITLE)[:140],
        badge=page.badge,
        theme_color_hex=_theme_color(),
        host=urlsplit(_origin()).netloc or None,
        footer_tags=page.tags,
    )
    return Response(
        content=image_bytes,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=86400, s-maxage=86400, stale-while-revalidate=604800",
        },
    )
