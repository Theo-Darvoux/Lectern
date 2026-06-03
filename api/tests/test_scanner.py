from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.config import settings
from app.core.exceptions import BadRequestError, ServiceUnavailableError
from app.core.scanner import MalwareScanner

# ── YARA tests ──


async def test_yara_scan_clean() -> None:
    scanner = MalwareScanner()
    scanner.rules = MagicMock()
    scanner.rules.match.return_value = []
    # Internal method test
    result = await scanner._scan_yara(b"clean file content", "test.pdf")
    assert result is None


async def test_yara_scan_match() -> None:
    scanner = MalwareScanner()
    scanner.rules = MagicMock()
    match = MagicMock()
    match.rule = "EICAR_test_file"
    scanner.rules.match.return_value = [match]
    result = await scanner._scan_yara(b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR", "test.txt")
    assert result == "EICAR_test_file"


async def test_yara_scan_multiple_matches_returns_first() -> None:
    scanner = MalwareScanner()
    scanner.rules = MagicMock()
    match1 = MagicMock()
    match1.rule = "PE_in_non_executable"
    match2 = MagicMock()
    match2.rule = "Embedded_Shellcode_Patterns"
    scanner.rules.match.return_value = [match1, match2]
    result = await scanner._scan_yara(b"MZ\x90\x00PE\x00\x00", "test.pdf")
    assert result == "PE_in_non_executable"


async def test_yara_scan_not_initialized() -> None:
    scanner = MalwareScanner()
    # scanner.rules is None by default
    with pytest.raises(RuntimeError, match="not initialized"):
        await scanner._scan_yara(b"data", "test.pdf")


# ── init_scanner tests ──


def test_init_scanner_missing_dir(tmp_path: object) -> None:
    with patch("app.core.scanner.settings") as mock_settings:
        mock_settings.yara_rules_dir = "/nonexistent/path"
        scanner = MalwareScanner()
        with pytest.raises(RuntimeError, match="not found"):
            scanner.initialize()


def test_init_scanner_empty_dir(tmp_path) -> None:
    with patch("app.core.scanner.settings") as mock_settings:
        mock_settings.yara_rules_dir = str(tmp_path)
        scanner = MalwareScanner()
        with pytest.raises(RuntimeError, match="No YARA rule files"):
            scanner.initialize()


# ── MalwareBazaar tests ──


async def test_hash_lookup_clean() -> None:
    scanner = MalwareScanner()
    scanner.client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"query_status": "hash_not_found"}
    scanner.client.post = AsyncMock(return_value=mock_response)

    result = await scanner.check_malwarebazaar("abc123", "test.pdf")
    assert result is None


async def test_hash_lookup_no_results() -> None:
    scanner = MalwareScanner()
    scanner.client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"query_status": "no_results"}
    scanner.client.post = AsyncMock(return_value=mock_response)

    result = await scanner.check_malwarebazaar("abc123", "test.pdf")
    assert result is None


async def test_hash_lookup_found() -> None:
    scanner = MalwareScanner()
    scanner.client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "query_status": "ok",
        "data": [{"signature": "Emotet", "file_name": "malware.exe"}],
    }
    scanner.client.post = AsyncMock(return_value=mock_response)

    result = await scanner.check_malwarebazaar("abc123", "test.pdf")
    assert result == "Emotet"


async def test_hash_lookup_timeout() -> None:
    scanner = MalwareScanner()
    scanner.client = MagicMock()
    scanner.client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))

    # Default is fail-closed: timeout should propagate as an exception
    with pytest.raises(httpx.TimeoutException):
        await scanner.check_malwarebazaar("abc123", "test.pdf")


async def test_hash_lookup_timeout_fail_soft() -> None:
    scanner = MalwareScanner()
    scanner.client = MagicMock()
    scanner.client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))

    # Explicit fail-soft mode returns None on timeout
    with patch.object(settings, "malwarebazaar_fail_closed", False):
        result = await scanner.check_malwarebazaar("abc123", "test.pdf")
        assert result is None


async def test_hash_lookup_http_error() -> None:
    scanner = MalwareScanner()
    scanner.client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 500
    scanner.client.post = AsyncMock(return_value=mock_response)

    # Fail-soft: returns None on HTTP error
    result = await scanner.check_malwarebazaar("abc123", "test.pdf")
    assert result is None


async def test_hash_lookup_unexpected_status() -> None:
    scanner = MalwareScanner()
    scanner.client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"query_status": "illegal_hash"}
    scanner.client.post = AsyncMock(return_value=mock_response)

    # Fail-soft: returns None on unexpected status
    result = await scanner.check_malwarebazaar("abc123", "test.pdf")
    assert result is None


async def test_hash_lookup_invalid_json() -> None:
    scanner = MalwareScanner()
    scanner.client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.side_effect = ValueError("Invalid JSON")
    scanner.client.post = AsyncMock(return_value=mock_response)

    # Fail-soft: returns None on invalid JSON
    result = await scanner.check_malwarebazaar("abc123", "test.pdf")
    assert result is None


async def test_hash_lookup_not_initialized() -> None:
    scanner = MalwareScanner()
    # scanner.client is None by default
    # Fail-soft: returns None if not initialized
    result = await scanner.check_malwarebazaar("abc123", "test.pdf")
    assert result is None


# ── Combined scan tests ──


async def test_combined_scan_both_clean() -> None:
    scanner = MalwareScanner()
    scanner.check_malwarebazaar = AsyncMock(return_value=None)  # type: ignore[method-assign]
    scanner._scan_yara = AsyncMock(return_value=None)  # type: ignore[method-assign]
    # Should not raise
    with patch.object(settings, "bazaar_async_enabled", False):
        await scanner.scan_file(b"clean content", "test.pdf")


async def test_combined_scan_yara_positive() -> None:
    scanner = MalwareScanner()
    scanner.check_malwarebazaar = AsyncMock(return_value=None)  # type: ignore[method-assign]
    scanner._scan_yara = AsyncMock(return_value="EICAR_test_file")  # type: ignore[method-assign]
    with patch.object(settings, "bazaar_async_enabled", False):
        with pytest.raises(BadRequestError, match="ERR_MALWARE_DETECTED"):
            await scanner.scan_file(b"content", "test.pdf")


async def test_combined_scan_bazaar_positive() -> None:
    scanner = MalwareScanner()
    scanner.check_malwarebazaar = AsyncMock(return_value="Emotet")  # type: ignore[method-assign]
    scanner._scan_yara = AsyncMock(return_value=None)  # type: ignore[method-assign]
    with patch.object(settings, "bazaar_async_enabled", False):
        with pytest.raises(BadRequestError, match="ERR_MALWARE_DETECTED"):
            await scanner.scan_file(b"content", "test.pdf")


async def test_combined_scan_both_positive() -> None:
    scanner = MalwareScanner()
    scanner.check_malwarebazaar = AsyncMock(return_value="Emotet")  # type: ignore[method-assign]
    scanner._scan_yara = AsyncMock(return_value="PE_in_non_executable")  # type: ignore[method-assign]
    with patch.object(settings, "bazaar_async_enabled", False):
        with pytest.raises(BadRequestError, match="ERR_MALWARE_DETECTED"):
            await scanner.scan_file(b"content", "test.pdf")


async def test_combined_scan_yara_error_fails_closed() -> None:
    scanner = MalwareScanner()
    scanner.check_malwarebazaar = AsyncMock(return_value=None)  # type: ignore[method-assign]
    scanner._scan_yara = AsyncMock(side_effect=RuntimeError("YARA crashed"))  # type: ignore[method-assign]
    with patch.object(settings, "bazaar_async_enabled", False):
        with pytest.raises(ServiceUnavailableError, match="fail-closed"):
            await scanner.scan_file(b"content", "test.pdf")


async def test_combined_scan_bazaar_error_fails_closed() -> None:
    scanner = MalwareScanner()
    scanner.check_malwarebazaar = AsyncMock(  # type: ignore[method-assign]
        side_effect=ServiceUnavailableError("MalwareBazaar down")
    )
    scanner._scan_yara = AsyncMock(return_value=None)  # type: ignore[method-assign]
    with patch.object(settings, "bazaar_async_enabled", False):
        with pytest.raises(ServiceUnavailableError, match="fail-closed"):
            await scanner.scan_file(b"content", "test.pdf")


async def test_combined_scan_both_error_fails_closed() -> None:
    scanner = MalwareScanner()
    scanner.check_malwarebazaar = AsyncMock(  # type: ignore[method-assign]
        side_effect=ServiceUnavailableError("MalwareBazaar down")
    )
    scanner._scan_yara = AsyncMock(side_effect=RuntimeError("YARA crashed"))  # type: ignore[method-assign]
    with patch.object(settings, "bazaar_async_enabled", False):
        with pytest.raises(ServiceUnavailableError, match="fail-closed"):
            await scanner.scan_file(b"content", "test.pdf")


# ── Path-based scan tests ──


async def test_yara_scan_path_match(tmp_path) -> None:
    scanner = MalwareScanner()
    scanner.rules = MagicMock()
    match = MagicMock()
    match.rule = "EICAR_test_file"
    scanner.rules.match.return_value = [match]

    test_file = tmp_path / "test.txt"
    test_file.write_bytes(b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR")

    result = await scanner._scan_yara_path(test_file, "test.txt")
    assert result == "EICAR_test_file"
    # verify filepath argument was used
    scanner.rules.match.assert_called_once()
    assert scanner.rules.match.call_args[1]["filepath"] == str(test_file)


async def test_combined_scan_path_both_clean(tmp_path) -> None:
    scanner = MalwareScanner()
    scanner.check_malwarebazaar = AsyncMock(return_value=None)  # type: ignore[method-assign]
    scanner._scan_yara_path = AsyncMock(return_value=None)  # type: ignore[method-assign]

    test_file = tmp_path / "test.pdf"
    test_file.write_bytes(b"clean content")

    # Should not raise
    with patch.object(settings, "bazaar_async_enabled", False):
        await scanner.scan_file_path(test_file, "test.pdf")
