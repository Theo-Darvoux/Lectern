"""Tests for the page metadata (/api/og/meta) the web server renders into its <head>,
and the preview banner generator (/api/og/image)."""

from __future__ import annotations

import uuid
from unittest.mock import patch

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.directory import Directory, DirectoryType
from app.models.material import Material
from app.models.user import User


async def test_og_meta_falls_back_to_default_branding(client: AsyncClient) -> None:
    from app.config import settings

    with patch.object(settings, "frontend_url", "https://wiki.example.com/"):
        resp = await client.get("/api/og/meta", params={"path": "/"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["site_name"] == "Lectern"
    assert body["title"] == "Lectern"
    assert body["og_title"] == "Lectern"
    assert body["type"] == "website"
    assert body["theme_color"] == settings.primary_color
    assert body["url"] == "https://wiki.example.com/"
    # Site-level pages share one banner URL, without a path.
    assert body["image"] == "https://wiki.example.com/api/og/image"
    assert (body["image_width"], body["image_height"]) == (1200, 630)


async def test_og_meta_reflects_env_branding(client: AsyncClient) -> None:
    from app.config import settings

    with (
        patch.object(settings, "frontend_url", "https://wiki.example.com"),
        patch.object(settings, "site_name", "Mon Wiki"),
        patch.object(settings, "site_description", "Cours de la promo 2026"),
        patch.object(settings, "og_image_url", "https://files.example.com/branding/og-image.webp"),
    ):
        resp = await client.get("/api/og/meta", params={"path": "/popular/"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Mon Wiki"
    assert body["site_name"] == "Mon Wiki"
    assert body["description"] == "Cours de la promo 2026"
    assert body["image"] == "https://files.example.com/branding/og-image.webp"
    # A configured image can be any size.
    assert body["image_width"] is None
    assert body["url"] == "https://wiki.example.com/popular/"


async def test_og_meta_relative_image_resolves_against_frontend_url(client: AsyncClient) -> None:
    from app.config import settings

    with (
        patch.object(settings, "frontend_url", "https://wiki.example.com"),
        patch.object(settings, "og_image_url", "/branding/og.png"),
    ):
        resp = await client.get("/api/og/meta")
    assert resp.json()["image"] == "https://wiki.example.com/branding/og.png"


async def test_og_meta_ignores_the_host_header(client: AsyncClient) -> None:
    from app.config import settings

    with patch.object(settings, "frontend_url", "https://wiki.example.com"):
        resp = await client.get("/api/og/meta", headers={"Host": "evil.example.net"})
    assert "evil.example.net" not in resp.text


async def test_og_meta_per_resource_directory_title(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    from app.config import settings

    db_session.add(
        Directory(
            id=uuid.uuid4(),
            name="Mathématiques",
            slug="maths",
            type=DirectoryType.FOLDER,
            description="Tous les cours de maths",
        )
    )
    await db_session.commit()

    with (
        patch.object(settings, "frontend_url", "https://wiki.example.com"),
        patch.object(settings, "site_name", "Mon Wiki"),
        patch.object(settings, "site_description", "Default desc"),
    ):
        resp = await client.get("/api/og/meta", params={"path": "/browse/maths/"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Mathématiques • Mon Wiki"
    assert body["og_title"] == "Mathématiques"
    assert body["description"] == "Tous les cours de maths"
    assert body["site_name"] == "Mon Wiki"
    assert body["image"] == "https://wiki.example.com/api/og/image?path=/browse/maths/"


async def test_og_meta_custom_env_overrides(client: AsyncClient) -> None:
    from app.config import settings

    with (
        patch.object(settings, "site_name", "INTellect"),
        patch.object(settings, "og_title", "INTellect — Cours & Annales"),
        patch.object(settings, "og_description", "Plateforme collaborative de cours et annales"),
        patch.object(settings, "og_theme_color", "#8b5cf6"),
        patch.object(settings, "og_site_name", "INTellect Club Code"),
        patch.object(settings, "og_locale", "fr_FR"),
        patch.object(settings, "og_twitter_site", "@ClubCode"),
        patch.object(settings, "og_twitter_creator", "@ClubCodeDev"),
    ):
        resp = await client.get("/api/og/meta", params={"path": "/"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "INTellect — Cours & Annales"
    assert body["og_title"] == "INTellect — Cours & Annales"
    assert body["theme_color"] == "#8b5cf6"
    assert body["site_name"] == "INTellect Club Code"
    assert body["description"] == "Plateforme collaborative de cours et annales"
    assert body["locale"] == "fr_FR"
    assert body["twitter_site"] == "@ClubCode"
    assert body["twitter_creator"] == "@ClubCodeDev"


async def test_og_meta_rejects_a_path_without_leading_slash(client: AsyncClient) -> None:
    resp = await client.get("/api/og/meta", params={"path": "https://evil.example.net"})
    assert resp.status_code == 422


async def test_og_image_endpoint(client: AsyncClient) -> None:
    resp = await client.get("/api/og/image", params={"path": "/"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert "public, max-age=" in resp.headers.get("cache-control", "")
    assert len(resp.content) > 1000  # valid PNG binary content
    # PNG signature check: \x89PNG\r\n\x1a\n
    assert resp.content.startswith(b"\x89PNG\r\n\x1a\n")


async def test_og_image_ignores_free_text_parameters(client: AsyncClient) -> None:
    """The banner text comes from the resolved path only, never from the query."""
    with patch("app.routers.og.generate_og_image", return_value=b"png") as render:
        resp = await client.get("/api/og/image", params={"path": "/", "title": "Mot de passe"})
    assert resp.status_code == 200
    assert "Mot de passe" not in render.call_args.kwargs.values()


async def test_og_meta_qcm_and_user_profile_resources(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        email="student@example.com",
        display_name="Alice Dupont",
        bio="Étudiante passionnée d'informatique",
    )
    db_session.add(user)

    qcm_id = uuid.uuid4()
    qcm = Material(
        id=qcm_id,
        title="QCM Réseaux S2",
        slug="qcm-reseaux-s2",
        type="qcm",
        description="Quiz d'entraînement sur les protocoles TCP/IP",
    )
    db_session.add(qcm)
    await db_session.commit()

    resp_user = await client.get("/api/og/meta", params={"path": f"/profile/{user_id}"})
    assert resp_user.status_code == 200
    assert resp_user.json()["og_title"] == "Alice Dupont"
    assert resp_user.json()["description"] == "Étudiante passionnée d'informatique"

    resp_qcm = await client.get("/api/og/meta", params={"path": f"/qcm/{qcm_id}"})
    assert resp_qcm.status_code == 200
    assert resp_qcm.json()["og_title"] == "QCM Réseaux S2"
    assert resp_qcm.json()["type"] == "article"
    assert resp_qcm.json()["description"] == "Quiz d'entraînement sur les protocoles TCP/IP"
