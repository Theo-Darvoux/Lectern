from __future__ import annotations

import gzip
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.common.exceptions import BadRequestError
from app.core.media.mimetypes import MimeRegistry, guess_mime_from_bytes
from app.models.user import User
from app.routers.materials import _decompress_gzip_text, get_material_text_content


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
async def test_text_endpoint_rejects_gzip_bomb_with_controlled_error() -> None:
    compressed = gzip.compress(b"A" * (10 * 1024 * 1024 + 1), compresslevel=9)
    material = {
        "current_version_info": {
            "file_key": "cas/gzip-bomb",
            "file_name": "legacy.txt.gz",
            "file_mime_type": "application/gzip",
        }
    }
    with (
        patch(
            "app.routers.materials.get_material_with_version",
            new=AsyncMock(return_value=material),
        ),
        patch("app.routers.materials.read_full_object", new=AsyncMock(return_value=compressed)),
        pytest.raises(BadRequestError, match="exceeds"),
    ):
        await get_material_text_content(
            "00000000-0000-0000-0000-000000000001",
            cast(User, SimpleNamespace(id=None)),
            cast(AsyncSession, SimpleNamespace()),
        )
