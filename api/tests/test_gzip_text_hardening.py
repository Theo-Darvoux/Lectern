from __future__ import annotations

import gzip
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.common.exceptions import BadRequestError
from app.core.media.mimetypes import MimeRegistry, guess_mime_from_bytes
from app.models.user import User
from app.routers.materials import _decompress_gzip_text, get_material_text_content
from tests.test_material_text_content import fake_stream_object


def test_bounded_gzip_decoder_accepts_small_legacy_text() -> None:
    payload = b"small legacy text\n"
    assert _decompress_gzip_text(gzip.compress(payload), max_output_bytes=1024) == payload


def test_bounded_gzip_decoder_rejects_high_expansion_payload() -> None:
    compressed = gzip.compress(b"A" * (256 * 1024), compresslevel=9)
    with pytest.raises(BadRequestError, match="exceeds"):
        _decompress_gzip_text(compressed, max_output_bytes=64 * 1024)


def test_bounded_gzip_decoder_detects_limit_crossing_on_later_read() -> None:
    compressed = gzip.compress(b"B" * (64 * 1024 + 1), compresslevel=9)
    with pytest.raises(BadRequestError, match="exceeds"):
        _decompress_gzip_text(compressed, max_output_bytes=64 * 1024)


def test_bounded_gzip_decoder_rejects_invalid_deflate_stream() -> None:
    malformed = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff\x07\x00"
    with pytest.raises(BadRequestError, match="Failed to decompress"):
        _decompress_gzip_text(malformed, max_output_bytes=1024)


def test_gzip_magic_is_authoritative_and_cannot_fall_back_to_text_extension() -> None:
    magic_mime = guess_mime_from_bytes(gzip.compress(b"disguised"))
    assert magic_mime == "application/gzip"
    assert MimeRegistry.get_authoritative_mime("notes.txt", magic_mime) == "application/gzip"
    assert not MimeRegistry.is_allowed_mime(magic_mime)


@pytest.mark.asyncio
async def test_text_endpoint_bounds_gzip_bomb_output() -> None:
    compressed = gzip.compress(b"A" * (10 * 1024 * 1024 + 1), compresslevel=9)
    version = SimpleNamespace(
        file_key="cas/gzip-bomb",
        file_name="legacy.txt.gz",
        file_mime_type="application/gzip",
    )
    with (
        patch(
            "app.routers.materials.get_material_file_info",
            new=AsyncMock(return_value=version),
        ),
        patch("app.routers.materials.stream_object", new=fake_stream_object(compressed)),
    ):
        response = await get_material_text_content(
            "00000000-0000-0000-0000-000000000001",
            cast(Request, SimpleNamespace(headers={})),
            cast(User, SimpleNamespace(id=None)),
            cast(AsyncSession, SimpleNamespace()),
        )
    # Decompression stops at the viewer limit instead of inflating the whole bomb.
    assert response.headers["X-Content-Truncated"] == "1"
    assert len(response.body) == 10 * 1024 * 1024


@pytest.mark.asyncio
async def test_text_endpoint_rejects_invalid_gzip_stream() -> None:
    malformed = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff\x07\x00"
    version = SimpleNamespace(
        file_key="cas/bad-gzip", file_name="notes.txt", file_mime_type="text/plain"
    )
    with (
        patch(
            "app.routers.materials.get_material_file_info",
            new=AsyncMock(return_value=version),
        ),
        patch("app.routers.materials.stream_object", new=fake_stream_object(malformed)),
        pytest.raises(BadRequestError, match="Failed to decompress"),
    ):
        await get_material_text_content(
            "00000000-0000-0000-0000-000000000001",
            cast(Request, SimpleNamespace(headers={})),
            cast(User, SimpleNamespace(id=None)),
            cast(AsyncSession, SimpleNamespace()),
        )
