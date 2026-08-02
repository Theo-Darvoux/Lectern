import io
import struct
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.core.common.upload_limits import upload_size_limit
from app.core.security.polyglot import check_polyglot
from app.workers.upload.stages.download import run_download_and_validate

_MIB = 1024 * 1024
_EOCD = struct.Struct("<4s4H2LH")


def _jpeg_prefix() -> bytes:
    return b"\xff\xd8\xff\xe0" + b"A" * 200


def test_appended_zip_with_trailing_bytes_is_rejected(tmp_path: Path) -> None:
    payload = io.BytesIO(_jpeg_prefix())
    with zipfile.ZipFile(payload, "a", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("payload.txt", "payload")

    path = tmp_path / "polyglot.jpg"
    path.write_bytes(payload.getvalue() + b"trailing-junk")

    with pytest.raises(ValueError, match="extractable ZIP payload"):
        check_polyglot(path, "image/jpeg")



def test_encrypted_appended_zip_is_rejected(tmp_path: Path) -> None:
    payload = io.BytesIO(_jpeg_prefix())
    with zipfile.ZipFile(payload, "a", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("payload.txt", "payload")

    data = bytearray(payload.getvalue())
    local = data.index(b"PK\x03\x04")
    central = data.index(b"PK\x01\x02")
    local_flags = int.from_bytes(data[local + 6 : local + 8], "little") | 0x1
    central_flags = int.from_bytes(data[central + 8 : central + 10], "little") | 0x1
    data[local + 6 : local + 8] = local_flags.to_bytes(2, "little")
    data[central + 8 : central + 10] = central_flags.to_bytes(2, "little")

    path = tmp_path / "encrypted-polyglot.jpg"
    path.write_bytes(data)

    with pytest.raises(ValueError, match="extractable ZIP payload"):
        check_polyglot(path, "image/jpeg")

def test_truncated_central_directory_is_not_treated_as_zip(tmp_path: Path) -> None:
    fake_tail = b"PK\x01\x02" + _EOCD.pack(
        b"PK\x05\x06",
        0,
        0,
        1,
        1,
        4,
        0,
        0,
    )
    path = tmp_path / "clean.jpg"
    path.write_bytes(_jpeg_prefix() + fake_tail)

    check_polyglot(path, "image/jpeg")


def test_marker_only_zip64_locator_is_not_treated_as_zip(tmp_path: Path) -> None:
    fake_tail = b"PK\x06\x07" + b"\x00" * 16 + _EOCD.pack(
        b"PK\x05\x06",
        0,
        0,
        0xFFFF,
        0xFFFF,
        0xFFFFFFFF,
        0xFFFFFFFF,
        0,
    )
    path = tmp_path / "clean.jpg"
    path.write_bytes(_jpeg_prefix() + fake_tail)

    check_polyglot(path, "image/jpeg")


def test_upload_size_limit_uses_category_over_global_default() -> None:
    video_limit, video_is_global = upload_size_limit("video/mp4")
    pdf_limit, pdf_is_global = upload_size_limit("application/pdf")
    unknown_limit, unknown_is_global = upload_size_limit("application/octet-stream")

    assert video_limit == settings.max_video_size_mb * _MIB
    assert not video_is_global
    assert pdf_limit == settings.max_document_size_mb * _MIB
    assert not pdf_is_global
    assert unknown_limit == settings.max_file_size_mb * _MIB
    assert unknown_is_global


@pytest.mark.asyncio
async def test_worker_download_uses_declared_type_limit(tmp_path: Path) -> None:
    observed: dict[str, int] = {}

    async def stop_after_limit_check(*_args, **kwargs):
        observed["max_bytes"] = kwargs["max_bytes"]
        raise LookupError("stop after download limit assertion")

    size = 150 * _MIB
    with (
        patch(
            "app.workers.upload.stages.download.get_object_info",
            new=AsyncMock(return_value={"size": size}),
        ),
        patch(
            "app.workers.upload.stages.download.download_file_with_hash",
            new=AsyncMock(side_effect=stop_after_limit_check),
        ),
        patch("app.workers.upload.stages.download.ensure_disk_space"),
    ):
        with pytest.raises(LookupError, match="stop after download limit assertion"):
            await run_download_and_validate(
                tmp_path / "download.bin",
                "quarantine/user/upload/video.mp4",
                "video.mp4",
                "video/mp4",
                None,
                "upload-id",
            )

    assert observed["max_bytes"] == settings.max_video_size_mb * _MIB
