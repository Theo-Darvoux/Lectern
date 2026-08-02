import asyncio
import logging
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Annotated, Any, cast

import httpx
import yara
from fastapi import Depends, Request

from app.config import settings
from app.core.common.exceptions import BadRequestError, ServiceUnavailableError

logger = logging.getLogger(__name__)


class MalwareScanner:
    """Dependency-injectable malware scanner (YARA + MalwareBazaar)."""

    def __init__(self) -> None:
        self.rules: yara.Rules | None = None
        self.client: httpx.AsyncClient | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="yara-scan")
        self._scan_slot: asyncio.Semaphore | None = None

    @property
    def scan_slot(self) -> asyncio.Semaphore:
        """Lazily initialize the Semaphore on the active event loop."""
        if self._scan_slot is None:
            self._scan_slot = asyncio.Semaphore(1)
        return self._scan_slot

    @property
    def initialized(self) -> bool:
        return self.rules is not None

    def _reset_executor(self) -> None:
        """Recreate the single-worker thread pool if a native thread hangs or times out."""
        logger.warning("Resetting YARA scanner ThreadPoolExecutor after thread timeout/error")
        old_executor = self._executor
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="yara-scan")
        old_executor.shutdown(wait=False, cancel_futures=True)

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
        self.client = httpx.AsyncClient(timeout=settings.malwarebazaar_timeout)
        logger.info("Scanner: compiled %d YARA rule file(s) from %s", len(rule_files), rules_dir)

    async def close(self) -> None:
        """Shut down the shared HTTP client."""
        if self.client:
            await self.client.aclose()
            self.client = None
        self._executor.shutdown(wait=False, cancel_futures=True)

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

    async def _run_yara_match(
        self,
        match_callable: Callable[[], list[Any]],
        filename: str,
    ) -> str | None:
        """Execute a YARA match callable in a thread executor with timeout."""
        loop = asyncio.get_running_loop()
        async with self.scan_slot:
            try:
                matches: list[Any] = await asyncio.wait_for(
                    loop.run_in_executor(self._executor, match_callable),
                    timeout=settings.yara_scan_timeout + 5,
                )
            except TimeoutError:
                logger.error("YARA scan timed out for %s; resetting thread executor.", filename)
                self._reset_executor()
                raise
            except Exception:
                self._reset_executor()
                raise
        if matches:
            rule_names = [m.rule for m in matches]
            logger.warning("YARA match in %s: %s", filename, ", ".join(rule_names))
            return cast(str, rule_names[0])
        return None

    async def _scan_yara(self, file_bytes: bytes, filename: str) -> str | None:
        """Match file bytes against compiled YARA rules. Runs in thread executor."""
        if self.rules is None:
            raise RuntimeError("Scanner YARA rules not initialized")
        rules = self.rules
        return await self._run_yara_match(
            lambda: rules.match(data=file_bytes, timeout=settings.yara_scan_timeout),
            filename,
        )

    async def _scan_yara_path(self, file_path: Path, filename: str) -> str | None:
        """Match file on disk against compiled YARA rules. Runs in thread executor."""
        if self.rules is None:
            raise RuntimeError("Scanner YARA rules not initialized")
        rules = self.rules
        return await self._run_yara_match(
            lambda: rules.match(filepath=str(file_path), timeout=settings.yara_scan_timeout),
            filename,
        )

    async def check_malwarebazaar(
        self,
        sha256: str,
        filename: str,
        *,
        fail_closed: bool | None = None,
    ) -> str | None:
        """Query MalwareBazaar for known malware by SHA-256 hash.

        Returns the threat signature name if flagged, or ``None`` only when the
        service explicitly reports the hash as absent. By default, availability
        errors follow ``settings.malwarebazaar_fail_closed``. Callers that need
        to distinguish an unavailable service from an explicit clean result can
        pass ``fail_closed=True`` and apply their own policy after catching the
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

        status = body.get("query_status")

        if status in ("hash_not_found", "no_results"):
            return None

        if status == "ok":
            data = body.get("data", [{}])
            if isinstance(data, list) and data:
                threat = data[0].get("signature") or data[0].get("file_name", "unknown")
            else:
                threat = "known malware"
            logger.warning("MalwareBazaar hit for %s (sha256=%s): %s", filename, sha256, threat)
            return cast(str, threat)

        message = f"MalwareBazaar returned unexpected status {status!r}"
        if effective_fail_closed:
            raise ServiceUnavailableError(message)
        logger.warning("%s. Skipping check.", message)
        return None


# ────────────────────────────────────────────────────────────────────────────────
# FastAPI dependency
# ────────────────────────────────────────────────────────────────────────────────


def get_scanner(request: Request) -> MalwareScanner:
    """Retrieve the scanner instance stored on app state at startup."""
    return cast(MalwareScanner, request.app.state.scanner)


ScannerDep = Annotated[MalwareScanner, Depends(get_scanner)]
