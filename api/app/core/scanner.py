import asyncio
import hashlib
import logging
import warnings
from pathlib import Path
from typing import Annotated, cast

import httpx
from fastapi import Depends, Request

import yara
from app.config import settings
from app.core.exceptions import BadRequestError, ServiceUnavailableError

logger = logging.getLogger("wikint")


class MalwareScanner:
    """Dependency-injectable malware scanner (YARA + MalwareBazaar)."""

    def __init__(self) -> None:
        self.rules: yara.Rules | None = None  # type: ignore[name-defined]
        self.client: httpx.AsyncClient | None = None

    @property
    def initialized(self) -> bool:
        return self.rules is not None

    def initialize(self) -> None:
        """Compile YARA rules from disk. Fails hard if no rules found."""
        rules_dir = Path(settings.yara_rules_dir)
        if not rules_dir.is_dir():
            raise RuntimeError(f"YARA rules directory not found: {rules_dir}")

        rule_files = {
            f.stem: str(f)
            for f in sorted(rules_dir.rglob("*"))
            if f.suffix in (".yar", ".yara") and f.is_file()
        }

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

    async def scan_file(
        self,
        file_bytes: bytes,
        filename: str,
        *,
        bazaar_hash: str | None = None,
    ) -> None:
        """Run YARA scan. When bazaar_async_enabled=True, Bazaar is skipped here and
        run asynchronously by the check_bazaar background worker after promotion.
        """
        if bazaar_hash is not None:
            sha256 = bazaar_hash
        else:
            sha256 = await asyncio.to_thread(lambda: hashlib.sha256(file_bytes).hexdigest())

        if settings.bazaar_async_enabled:
            # Async mode: YARA-only gate — Bazaar runs in background after promotion.
            try:
                yara_result = await self._scan_yara(file_bytes, filename)
            except Exception as e:
                logger.error("YARA scan failed for %s: %s", filename, e)
                raise ServiceUnavailableError(
                    "Malware scan is temporarily unavailable (fail-closed). Please retry in a few moments."
                )

            if yara_result is not None:
                logger.warning("YARA match in %s: %s", filename, yara_result)
                raise BadRequestError(f"ERR_MALWARE_DETECTED: {yara_result}")
            return

        # Legacy synchronous mode: YARA + Bazaar run concurrently.
        yara_result, bazaar_result = await asyncio.gather(  # type: ignore[assignment]
            self._scan_yara(file_bytes, filename),
            self.check_malwarebazaar(sha256, filename),
            return_exceptions=True,
        )

        # Check for exceptions first (fail-closed)
        errors = []
        if isinstance(yara_result, Exception):
            logger.error("YARA scan failed for %s: %s", filename, yara_result)
            errors.append("YARA")
        if isinstance(bazaar_result, Exception):
            logger.error(
                "MalwareBazaar lookup failed for %s: %s (%s)",
                filename,
                type(bazaar_result).__name__,
                bazaar_result,
            )
            errors.append("MalwareBazaar")

        if errors:
            raise ServiceUnavailableError(
                "Malware scan is temporarily unavailable (fail-closed). Please retry in a few moments."
            )

        # At this point neither result is an Exception (errors would have raised).
        threats: list[tuple[str, str]] = []
        if yara_result is not None and not isinstance(yara_result, Exception):
            threats.append(("YARA", str(yara_result)))
        if bazaar_result is not None and not isinstance(bazaar_result, Exception):
            threats.append(("MalwareBazaar", str(bazaar_result)))

        if threats:
            for source, threat in threats:
                logger.warning("Malware detected in %s by %s: %s", filename, source, threat)
            _, signature = threats[-1]
            raise BadRequestError(f"ERR_MALWARE_DETECTED: {signature}")

    async def scan_file_path(
        self,
        file_path: Path,
        filename: str,
        *,
        bazaar_hash: str | None = None,
    ) -> None:
        """Run YARA scan on a file path. When bazaar_async_enabled=True, Bazaar is skipped
        here and run asynchronously by the check_bazaar background worker after promotion.
        """
        if bazaar_hash is None:

            def _hash_file() -> str:
                hasher = hashlib.sha256()
                with open(file_path, "rb") as f:
                    for chunk in iter(lambda: f.read(64 * 1024), b""):
                        hasher.update(chunk)
                return hasher.hexdigest()

            bazaar_hash = await asyncio.to_thread(_hash_file)

        if bazaar_hash is None:
            raise RuntimeError("Malware hash calculation failed")

        if settings.bazaar_async_enabled:
            # Async mode: YARA-only gate — Bazaar runs in background after promotion.
            try:
                yara_result = await self._scan_yara_path(file_path, filename)
            except Exception as e:
                logger.error("YARA scan failed for %s: %s", filename, e)
                raise ServiceUnavailableError(
                    "Malware scan is temporarily unavailable (fail-closed). Please retry in a few moments."
                )

            if yara_result is not None:
                logger.warning("YARA match in %s: %s", filename, yara_result)
                raise BadRequestError(f"ERR_MALWARE_DETECTED: {yara_result}")
            return

        # Legacy synchronous mode: YARA + Bazaar run concurrently.
        yara_result, bazaar_result = await asyncio.gather(  # type: ignore[assignment]
            self._scan_yara_path(file_path, filename),
            self.check_malwarebazaar(bazaar_hash, filename),
            return_exceptions=True,
        )

        # Check for exceptions first (fail-closed)
        errors = []
        if isinstance(yara_result, Exception):
            logger.error("YARA scan failed for %s: %s", filename, yara_result)
            errors.append("YARA")
        if isinstance(bazaar_result, Exception):
            logger.error(
                "MalwareBazaar lookup failed for %s: %s (%s)",
                filename,
                type(bazaar_result).__name__,
                bazaar_result,
            )
            errors.append("MalwareBazaar")

        if errors:
            raise ServiceUnavailableError(
                "Malware scan is temporarily unavailable (fail-closed). Please retry in a few moments."
            )

        # At this point neither result is an Exception (errors would have raised).
        threats: list[tuple[str, str]] = []
        if yara_result is not None and not isinstance(yara_result, Exception):
            threats.append(("YARA", str(yara_result)))
        if bazaar_result is not None and not isinstance(bazaar_result, Exception):
            threats.append(("MalwareBazaar", str(bazaar_result)))

        if threats:
            for source, threat in threats:
                logger.warning("Malware detected in %s by %s: %s", filename, source, threat)
            _, signature = threats[-1]
            raise BadRequestError(f"ERR_MALWARE_DETECTED: {signature}")

    async def _scan_yara(self, file_bytes: bytes, filename: str) -> str | None:
        """Match file against compiled YARA rules. Runs in thread executor."""
        if self.rules is None:
            raise RuntimeError("Scanner YARA rules not initialized")
        rules = self.rules

        loop = asyncio.get_running_loop()
        matches = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: rules.match(data=file_bytes, timeout=settings.yara_scan_timeout),
            ),
            timeout=settings.yara_scan_timeout + 5,
        )

        if matches:
            rule_names = [m.rule for m in matches]
            logger.warning("YARA match in %s: %s", filename, ", ".join(rule_names))
            return cast(str, rule_names[0])
        return None

    async def _scan_yara_path(self, file_path: Path, filename: str) -> str | None:
        """Match file on disk against compiled YARA rules. Runs in thread executor."""
        if self.rules is None:
            raise RuntimeError("Scanner YARA rules not initialized")
        rules = self.rules

        loop = asyncio.get_running_loop()
        matches = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: rules.match(filepath=str(file_path), timeout=settings.yara_scan_timeout),
            ),
            timeout=settings.yara_scan_timeout + 5,
        )

        if matches:
            rule_names = [m.rule for m in matches]
            logger.warning("YARA match in %s: %s", filename, ", ".join(rule_names))
            return cast(str, rule_names[0])
        return None

    async def check_malwarebazaar(self, sha256: str, filename: str) -> str | None:
        """Query MalwareBazaar for known malware by SHA-256 hash.

        Returns the threat signature name if flagged, or None if clean.
        Network/timeout errors are re-raised so callers can handle fail-closed logic.

        Used both by the legacy synchronous scan path (bazaar_async_enabled=False) and
        by the check_bazaar background worker.
        """
        if self.client is None:
            logger.error("Scanner HTTP client not initialized")
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
            if settings.malwarebazaar_fail_closed:
                # Propagates to scan_file_path → errors list → ServiceUnavailableError
                raise
            logger.warning(
                "Malware scanner (MalwareBazaar) is temporarily unavailable: %s. "
                "Continuing with local scan results only.",
                e,
            )
            return None

        if resp.status_code != 200:
            logger.warning(
                "MalwareBazaar returned HTTP %d for %s — skipping external check.",
                resp.status_code,
                filename,
            )
            return None

        try:
            body = resp.json()
        except (ValueError, TypeError):
            logger.warning("MalwareBazaar returned invalid JSON for %s — skipping.", filename)
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

        logger.warning("MalwareBazaar unexpected status '%s' — skipping check.", status)
        return None


# ────────────────────────────────────────────────────────────────────────────────
# Dependency and Backward Compatibility
# ────────────────────────────────────────────────────────────────────────────────


def get_scanner(request: Request) -> MalwareScanner:
    """Retrieve the singleton scanner instance from app state."""
    return cast(MalwareScanner, request.app.state.scanner)


# Singleton instance for code outside FastAPI request context (e.g. background tasks)
_global_scanner: MalwareScanner | None = None


def init_scanner() -> None:
    """Backward compatibility: initializes global singleton."""
    global _global_scanner
    _global_scanner = MalwareScanner()
    _global_scanner.initialize()


async def close_scanner() -> None:
    """Backward compatibility: closes global singleton."""
    if _global_scanner:
        await _global_scanner.close()


async def scan_file(file_bytes: bytes, filename: str, *, bazaar_hash: str | None = None) -> None:
    """Backward compatibility wrapper — prefer scan_file_path() for large files.

    .. deprecated::
        ``scan_file(bytes)`` loads the entire file into memory and is
        memory-unsafe for large uploads.  Use ``scan_file_path(Path)`` instead.
    """
    warnings.warn(
        "scan_file(bytes) is deprecated and memory-unsafe for large files. "
        "Use MalwareScanner.scan_file_path(Path) instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    if _global_scanner is None:
        # Auto-init if accessed before main.py lifespan (useful for tests)
        init_scanner()

    if _global_scanner is None:
        raise RuntimeError("Malware scanner failed to initialize")
    await _global_scanner.scan_file(file_bytes, filename, bazaar_hash=bazaar_hash)


ScannerDep = Annotated[MalwareScanner, Depends(get_scanner)]
