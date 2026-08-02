from unittest.mock import AsyncMock, patch

import pytest

from app.core.security.file_security.errors import SanitizationError
from app.core.security.file_security.strip import (
    _require_sanitized_output,
    strip_metadata_file,
)


async def _return_path(path):
    return path


@pytest.mark.asyncio
async def test_required_sanitizer_rejects_original_path(tmp_path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"unsafe")

    with pytest.raises(SanitizationError, match="original unsanitized"):
        await _require_sanitized_output(
            source,
            _return_path(source),
            mime_type="application/pdf",
        )


@pytest.mark.asyncio
async def test_required_sanitizer_rejects_missing_output(tmp_path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"unsafe")
    missing = tmp_path / "missing.bin"

    with pytest.raises(SanitizationError, match="did not produce"):
        await _require_sanitized_output(
            source,
            _return_path(missing),
            mime_type="image/png",
        )


@pytest.mark.asyncio
async def test_ooxml_dispatcher_cannot_accept_original_path(tmp_path) -> None:
    mime_type = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    source = tmp_path / "document.docx"
    source.write_bytes(b"PK\x03\x04")

    with (
        patch(
            "app.core.security.file_security.strip._strip_ooxml_from_path",
            new_callable=AsyncMock,
            return_value=source,
        ),
        pytest.raises(SanitizationError, match="original unsanitized"),
    ):
        await strip_metadata_file(source, mime_type)
