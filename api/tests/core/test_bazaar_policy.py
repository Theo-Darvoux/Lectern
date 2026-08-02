from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.common.exceptions import ServiceUnavailableError
from app.core.security.scanner import MalwareScanner
from app.workers.check_bazaar import (
    _BAZAAR_CLEAN_PREFIX,
    _BAZAAR_SKIPPED_PREFIX,
    _CLEAN_TOMBSTONE_TTL,
    _SKIPPED_TOMBSTONE_TTL,
    check_bazaar,
)


@pytest.mark.asyncio
async def test_scanner_strict_override_rejects_http_error() -> None:
    scanner = MalwareScanner()
    response = MagicMock(status_code=503)
    scanner.client = MagicMock()
    scanner.client.post = AsyncMock(return_value=response)

    with (
        patch("app.core.security.scanner.settings") as mock_settings,
        pytest.raises(ServiceUnavailableError, match="HTTP 503"),
    ):
        mock_settings.malwarebazaar_fail_closed = False
        mock_settings.malwarebazaar_api_key = ""
        mock_settings.malwarebazaar_url = "https://example.invalid"
        await scanner.check_malwarebazaar(
            "a" * 64,
            "upload-id",
            fail_closed=True,
        )


@pytest.mark.asyncio
async def test_worker_unavailable_result_never_writes_clean_tombstone() -> None:
    sha256 = "b" * 64
    redis = AsyncMock()
    redis.get.return_value = None

    scanner = MagicMock()
    scanner.check_malwarebazaar = AsyncMock(
        side_effect=ServiceUnavailableError("Bazaar unavailable")
    )

    ctx = {"redis": redis, "scanner": scanner}

    with patch("app.workers.check_bazaar.settings") as mock_settings:
        mock_settings.malwarebazaar_fail_closed = False
        await check_bazaar(
            ctx,
            upload_id="upload-id",
            sha256=sha256,
            cas_s3_key="cas/object",
            user_id="user-id",
        )

    scanner.check_malwarebazaar.assert_awaited_once_with(
        sha256,
        "upload-id",
        fail_closed=True,
    )
    redis.set.assert_awaited_once_with(
        f"{_BAZAAR_SKIPPED_PREFIX}{sha256}",
        "1",
        ex=_SKIPPED_TOMBSTONE_TTL,
    )
    assert not any(
        call.args and call.args[0] == f"{_BAZAAR_CLEAN_PREFIX}{sha256}"
        for call in redis.set.await_args_list
    )


@pytest.mark.asyncio
async def test_worker_explicit_clean_result_writes_clean_tombstone() -> None:
    sha256 = "c" * 64
    redis = AsyncMock()
    redis.get.return_value = None

    scanner = MagicMock()
    scanner.check_malwarebazaar = AsyncMock(return_value=None)

    await check_bazaar(
        {"redis": redis, "scanner": scanner},
        upload_id="upload-id",
        sha256=sha256,
        cas_s3_key="cas/object",
        user_id="user-id",
    )

    scanner.check_malwarebazaar.assert_awaited_once_with(
        sha256,
        "upload-id",
        fail_closed=True,
    )
    redis.set.assert_awaited_once_with(
        f"{_BAZAAR_CLEAN_PREFIX}{sha256}",
        "1",
        ex=_CLEAN_TOMBSTONE_TTL,
    )
