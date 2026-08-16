"""Tests for the crawler-facing Open Graph renderer (/api/og) and dynamic banner generator (/api/og/image)."""

from __future__ import annotations

import uuid
from unittest.mock import patch

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.directory import Directory, DirectoryType
from app.models.material import Material
from app.models.user import User


async def test_og_falls_back_to_default_branding(client: AsyncClient) -> None:
    resp = await client.get("/api/og", headers={"X-Original-Path": "/"})
    assert resp.status_code == 200
    body = resp.text
    assert "<title>Lectern</title>" in body
    assert '<meta property="og:site_name" content="Lectern">' in body
    assert 'property="og:title" content="Lectern"' in body
    assert 'name="theme-color"' in body
    assert 'property="og:image"' in body
    assert "/api/og/image?" in body


async def test_og_reflects_admin_config_branding(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    from app.config import settings

    with (
        patch.object(settings, "site_name", "Mon Wiki"),
        patch.object(settings, "site_description", "Cours de la promo 2026"),
        patch.object(settings, "og_image_url", "https://files.example.com/branding/og-image.webp"),
    ):
        resp = await client.get(
            "/api/og",
            headers={
                "X-Original-Path": "/popular/",
                "Host": "wiki.example.com",
                "X-Forwarded-Proto": "https",
            },
        )
    assert resp.status_code == 200
    body = resp.text
    assert "<title>Mon Wiki</title>" in body
    assert '<meta property="og:site_name" content="Mon Wiki">' in body
    assert '<meta property="og:description" content="Cours de la promo 2026">' in body
    assert (
        '<meta property="og:image" content="https://files.example.com/branding/og-image.webp">'
        in body
    )
    assert '<meta property="og:url" content="https://wiki.example.com/popular/">' in body


async def test_og_per_resource_directory_title(
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
        patch.object(settings, "site_name", "Mon Wiki"),
        patch.object(settings, "site_description", "Default desc"),
    ):
        resp = await client.get("/api/og", headers={"X-Original-Path": "/browse/maths/"})
    assert resp.status_code == 200
    body = resp.text
    assert "Mathématiques" in body
    assert '<meta property="og:title" content="Mathématiques">' in body
    assert '<meta property="og:description" content="Tous les cours de maths">' in body
    assert '<meta property="og:site_name" content="Mon Wiki">' in body


async def test_og_custom_env_overrides(client: AsyncClient) -> None:
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
        resp = await client.get(
            "/api/og",
            headers={
                "X-Original-Path": "/",
                "Host": "intellect.clubcode.fr",
                "X-Forwarded-Proto": "https",
            },
        )
    assert resp.status_code == 200
    body = resp.text
    assert "<title>INTellect — Cours &amp; Annales</title>" in body
    assert '<meta name="theme-color" content="#8b5cf6">' in body
    assert '<meta property="og:site_name" content="INTellect Club Code">' in body
    assert '<meta property="og:title" content="INTellect — Cours &amp; Annales">' in body
    assert (
        '<meta property="og:description" content="Plateforme collaborative de cours et annales">'
        in body
    )
    assert '<meta property="og:locale" content="fr_FR">' in body
    assert '<meta name="twitter:site" content="@ClubCode">' in body
    assert '<meta name="twitter:creator" content="@ClubCodeDev">' in body
    assert '<meta property="og:image:width" content="1200">' in body
    assert '<meta property="og:image:height" content="630">' in body


async def test_og_dynamic_image_endpoint(client: AsyncClient) -> None:
    resp = await client.get(
        "/api/og/image",
        params={
            "title": "Algèbre Linéaire",
            "subtitle": "Tous les cours et exercices",
            "badge": "Mathématiques",
            "theme": "#8b5cf6",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert "public, max-age=" in resp.headers.get("cache-control", "")
    assert len(resp.content) > 1000  # valid PNG binary content
    # PNG signature check: \x89PNG\r\n\x1a\n
    assert resp.content.startswith(b"\x89PNG\r\n\x1a\n")


async def test_og_qcm_and_user_profile_resources(
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

    # Profile OG
    resp_user = await client.get("/api/og", headers={"X-Original-Path": f"/profile/{user_id}"})
    assert resp_user.status_code == 200
    assert "Alice Dupont" in resp_user.text
    assert (
        "Étudiante passionnée d&#x27;informatique" in resp_user.text
        or "Étudiante passionnée d'informatique" in resp_user.text
    )

    # QCM OG
    resp_qcm = await client.get("/api/og", headers={"X-Original-Path": f"/qcm/{qcm_id}"})
    assert resp_qcm.status_code == 200
    assert "QCM Réseaux S2" in resp_qcm.text
    assert (
        "Quiz d&#x27;entraînement sur les protocoles TCP/IP" in resp_qcm.text
        or "Quiz d'entraînement sur les protocoles TCP/IP" in resp_qcm.text
    )
