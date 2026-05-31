"""Tests for the crawler-facing Open Graph renderer (/api/og)."""

from __future__ import annotations

import uuid
from unittest.mock import patch

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.directory import Directory, DirectoryType


async def test_og_falls_back_to_default_branding(client: AsyncClient) -> None:
    resp = await client.get("/api/og", headers={"X-Original-Path": "/"})
    assert resp.status_code == 200
    body = resp.text
    assert "<title>WikINT</title>" in body
    assert '<meta property="og:site_name" content="WikINT">' in body
    assert 'property="og:title" content="WikINT"' in body


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
    # Site name is still present as og:site_name
    assert '<meta property="og:site_name" content="Mon Wiki">' in body
