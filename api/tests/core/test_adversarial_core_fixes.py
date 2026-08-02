import io
import zipfile
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.core.security.polyglot import check_polyglot
from app.core.security.scanner import MalwareScanner


@pytest.mark.asyncio
async def test_yara_timeout_does_not_spawn_replacement_workers() -> None:
    scanner = MalwareScanner()
    native_future: Future[list[object]] = Future()

    with (
        patch.object(scanner._executor, "submit", return_value=native_future) as submit,
        patch(
            "app.core.security.scanner.asyncio.wait_for",
            new=AsyncMock(side_effect=TimeoutError),
        ),
    ):
        with pytest.raises(TimeoutError):
            await scanner._run_yara_match(lambda: [], "stalled.bin")

        assert scanner._active_yara_future is native_future
        with pytest.raises(RuntimeError, match="previous YARA scan is still running"):
            await scanner._run_yara_match(lambda: [], "second.bin")
        submit.assert_called_once()

    native_future.set_result([])
    await scanner.close()


def test_invalid_eocd_bytes_do_not_make_media_a_polyglot(tmp_path: Path) -> None:
    path = tmp_path / "clean.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0" + b"A" * 200 + b"PK\x05\x06" + b"\xff" * 18)

    check_polyglot(path, "image/jpeg")


def test_valid_appended_zip_is_still_rejected(tmp_path: Path) -> None:
    payload = io.BytesIO(b"\xff\xd8\xff\xe0" + b"A" * 200)
    with zipfile.ZipFile(payload, "a", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("payload.txt", "payload")

    path = tmp_path / "polyglot.jpg"
    path.write_bytes(payload.getvalue())

    with pytest.raises(ValueError, match="valid ZIP End-of-Central-Directory"):
        check_polyglot(path, "image/jpeg")


def test_cas_duplicate_marker_without_record_fails_closed() -> None:
    script = Path("app/core/security/lua/cas_incr.lua").read_text(encoding="utf-8")
    duplicate_branch = script.split("if redis.call('EXISTS', KEYS[3]) == 1 then", 1)[1]
    duplicate_branch = duplicate_branch.split("end\nif not raw then", 1)[0]

    assert "if not raw then return -1 end" in duplicate_branch
    assert "if not raw then return 0 end" not in duplicate_branch
