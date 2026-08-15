import asyncio
import logging
from collections.abc import Awaitable
from pathlib import Path
from typing import Annotated, cast

import httpx
import yara
from fastapi import Depends, Request

from app.config import settings
from app.core.common.exceptions import BadRequestError, ServiceUnavailableError
from app.core.security.isolated_parser import scan_yara_isolated
from app.core.security.processing_paths import make_processing_temp_path

logger = logging.getLogger(__name__)


class MalwareScanner:
    """Dependency-injectable malware scanner (YARA + MalwareBazaar)."""

    def __init__(self) -> None:
        self.rules: yara.Rules | None = None
        self.client: httpx.AsyncClient | None = None
        self._compiled_rules_path: Path | None = None

    @property
    def initialized(self) -> bool:
        return self.rules is not None

    def initialize(self) -> None:
        """Compile YARA rules from disk. Fails hard if no rules found."""
        rules_dir = Path(settings.yara_rules_dir)
        if not rules_dir.is_dir():
            raise RuntimeError(f"YARA rules directory not found: {rules_dir}")

        rule_paths = [
            path
            for path in sorted(rules_dir.rglob("*"))
            if path.suffix in (".yar", ".yara") and path.is_file()
        ]
        # YARA's mapping keys are namespaces. File stems are not unique across
        # subdirectories, so stable ordinal namespaces prevent silent rule loss.
        rule_files = {f"rules_{index}": str(path) for index, path in enumerate(rule_paths)}

        if not rule_files:
            raise RuntimeError(f"No YARA rule files (*.yar, *.yara) found in {rules_dir}")

        self.rules = yara.compile(filepaths=rule_files)
        compiled_path = make_processing_temp_path(suffix=".yarac", prefix="yara-rules-")
        try:
            self.rules.save(str(compiled_path))
        except BaseException:
            compiled_path.unlink(missing_ok=True)
            raise
        self._compiled_rules_path = compiled_path
        self.client = httpx.AsyncClient(timeout=settings.malwarebazaar_timeout)
        logger.info("Scanner: compiled %d YARA rule file(s) from %s", len(rule_files), rules_dir)

    async def close(self) -> None:
        """Shut down the shared HTTP client and scanner executor."""
        if self.client:
            await self.client.aclose()
            self.client = None
        if self._compiled_rules_path is not None:
            self._compiled_rules_path.unlink(missing_ok=True)
            self._compiled_rules_path = None

    async def _run_scan_gate(
        self,
        yara_coro: Awaitable[str | None],
        filename: str,
    ) -> None:
        """Run YARA scan gate.

        Raises ServiceUnavailableError if the YARA scan fails,
        or BadRequestError if malware is detected.
        """
        try:
            yara_result = await yara_coro
        except Exception as e:
            logger.error("YARA scan failed for %s: %s", filename, e)
            raise ServiceUnavailableError(
                "Malware scan is temporarily unavailable. Please retry in a few moments."
            ) from e
        if yara_result is not None:
            raise BadRequestError(f"ERR_MALWARE_DETECTED: {yara_result}")

    async def scan_file(
        self,
        file_bytes: bytes,
        filename: str,
    ) -> None:
        """Run the local YARA gate; the pipeline applies the configured Bazaar policy."""
        await self._run_scan_gate(self._scan_yara(file_bytes, filename), filename)

    async def scan_file_path(
        self,
        file_path: Path,
        filename: str,
    ) -> None:
        """Run the local YARA gate; the pipeline applies the configured Bazaar policy."""
        await self._run_scan_gate(self._scan_yara_path(file_path, filename), filename)

    async def _scan_yara(self, file_bytes: bytes, filename: str) -> str | None:
        """Match bytes in a disposable isolated parser process."""
        if self.rules is None or self._compiled_rules_path is None:
            raise RuntimeError("Scanner YARA rules not initialized")
        file_path = make_processing_temp_path(suffix=Path(filename).suffix, prefix="yara-input-")
        try:
            await asyncio.to_thread(file_path.write_bytes, file_bytes)
            return await scan_yara_isolated(
                file_path,
                compiled_rules_path=self._compiled_rules_path,
                timeout=settings.yara_scan_timeout,
            )
        finally:
            file_path.unlink(missing_ok=True)

    async def _scan_yara_path(self, file_path: Path, filename: str) -> str | None:
        """Match a file in a disposable isolated parser process."""
        if self.rules is None or self._compiled_rules_path is None:
            raise RuntimeError("Scanner YARA rules not initialized")
        return await scan_yara_isolated(
            file_path,
            compiled_rules_path=self._compiled_rules_path,
            timeout=settings.yara_scan_timeout,
        )

    async def check_malwarebazaar(
        self,
        sha256: str,
        filename: str,
        *,
        fail_closed: bool | None = None,
    ) -> str | None:
        """Query MalwareBazaar for known malware by SHA-256 hash.

        Returns the threat signature name if flagged, or ``None`` when no
        threat is reported. In fail-open mode ``None`` can also mean that the
        service was unavailable. Callers that must distinguish those cases pass
        ``fail_closed=True`` and apply their own policy after catching the
        resulting exception.
        """
        effective_fail_closed = (
            settings.malwarebazaar_fail_closed if fail_closed is None else fail_closed
        )

        if self.client is None:
            message = "Scanner HTTP client not initialized"
            if effective_fail_closed:
                raise ServiceUnavailableError(message)
            logger.error(message)
            return None

        headers = {}
        if settings.malwarebazaar_api_key:
            headers["Auth-Key"] = settings.malwarebazaar_api_key

        try:
            resp = await self.client.post(
                settings.malwarebazaar_url,
                data={"query": "get_info", "hash": sha256},
                headers=headers,
            )
        except (httpx.TimeoutException, httpx.HTTPError) as e:
            if effective_fail_closed:
                raise
            logger.warning(
                "MalwareBazaar is temporarily unavailable: %s. "
                "Continuing with local scan results only.",
                e,
            )
            return None

        if resp.status_code != 200:
            message = f"MalwareBazaar returned HTTP {resp.status_code} for {filename}"
            if effective_fail_closed:
                raise ServiceUnavailableError(message)
            logger.warning("%s. Skipping external check.", message)
            return None

        try:
            body = resp.json()
        except (ValueError, TypeError):
            message = f"MalwareBazaar returned invalid JSON for {filename}"
            if effective_fail_closed:
                raise ServiceUnavailableError(message)
            logger.warning("%s. Skipping.", message)
            return None

        if not isinstance(body, dict):
            message = f"MalwareBazaar returned a non-object JSON response for {filename}"
            if effective_fail_closed:
                raise ServiceUnavailableError(message)
            logger.warning("%s. Skipping.", message)
            return None

        status = body.get("query_status")

        if status in ("hash_not_found", "no_results"):
            return None

        if status == "ok":
            data = body.get("data")
            if not isinstance(data, list) or not data or not isinstance(data[0], dict):
                message = f"MalwareBazaar returned malformed threat data for {filename}"
                if effective_fail_closed:
                    raise ServiceUnavailableError(message)
                logger.warning("%s. Skipping.", message)
                return None

            first = data[0]
            threat = first.get("signature") or first.get("file_name") or "known malware"
            logger.warning("MalwareBazaar hit for %s (sha256=%s): %s", filename, sha256, threat)
            return str(threat)

        message = f"MalwareBazaar returned unexpected status {status!r}"
        if effective_fail_closed:
            raise ServiceUnavailableError(message)
        logger.warning("%s. Skipping check.", message)
        return None


# FastAPI dependency


def get_scanner(request: Request) -> MalwareScanner:
    """Retrieve the scanner instance stored on app state at startup."""
    return cast(MalwareScanner, request.app.state.scanner)


ScannerDep = Annotated[MalwareScanner, Depends(get_scanner)]
