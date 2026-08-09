"""Distributed + local concurrency guards for heavy file operations."""

import asyncio
import logging
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from app.config import settings
from app.core.database.redis import (
    RedisSemaphoreUnavailableError,
    redis_client,
    redis_semaphore,
)
from app.core.security.sandbox import async_sandboxed_run

logger = logging.getLogger(__name__)

_SUBPROCESS_LIMIT = int(settings.global_max_subprocesses)
_IMAGE_CONCURRENCY_LIMIT = int(settings.max_concurrent_image_ops)

_local_subprocess_sem = asyncio.Semaphore(_SUBPROCESS_LIMIT)
_local_image_sem = asyncio.Semaphore(_IMAGE_CONCURRENCY_LIMIT)
_redis_unavailable_until: dict[str, float] = {"subprocess": 0.0, "image": 0.0}


@asynccontextmanager
async def _get_concurrency_guard(guard_type: str) -> AsyncIterator[None]:
    """Acquire a distributed semaphore with a local fallback in development, failing closed in production."""
    if guard_type not in {"subprocess", "image"}:
        raise ValueError(f"Unknown guard_type '{guard_type}'")

    global _redis_unavailable_until
    limit = _SUBPROCESS_LIMIT if guard_type == "subprocess" else _IMAGE_CONCURRENCY_LIMIT
    local_sem = _local_subprocess_sem if guard_type == "subprocess" else _local_image_sem

    if settings.environment != "production":
        async with local_sem:
            yield
        return

    loop = asyncio.get_running_loop()
    if loop.time() < _redis_unavailable_until.get(guard_type, 0.0):
        raise RedisSemaphoreUnavailableError(
            f"Redis semaphore unavailable for {guard_type} guard in production"
        )

    acquired = False
    body_exc: BaseException | None = None
    try:
        async with redis_semaphore(redis_client, f"heavy_ops:{guard_type}", limit=limit):
            acquired = True
            async with local_sem:
                try:
                    yield
                except BaseException as exc:
                    body_exc = exc
                    raise
            return
    except RedisSemaphoreUnavailableError as e:
        if acquired:
            if body_exc is not None and e is not body_exc:
                raise body_exc from e
            raise
        if settings.environment == "production":
            _redis_unavailable_until[guard_type] = loop.time() + 5.0
            logger.error(
                "Redis semaphore unavailable in production for %s guard; failing closed: %s",
                guard_type,
                e,
            )
            raise
        _redis_unavailable_until[guard_type] = loop.time() + 5.0
        logger.warning(
            "Redis unavailable for %s guard; using local limit for 5 seconds in development: %s",
            guard_type,
            e,
        )
    async with local_sem:
        yield


@asynccontextmanager
async def subprocess_guard() -> AsyncIterator[None]:
    """Concurrency guard for heavy sandboxed subprocess execution."""
    async with _get_concurrency_guard("subprocess"):
        yield


@asynccontextmanager
async def image_guard() -> AsyncIterator[None]:
    """Concurrency guard for heavy in-process image operations."""
    async with _get_concurrency_guard("image"):
        yield


async def run_managed_subprocess(
    cmd: list[str],
    timeout: int = 60,
    check: bool = True,
    *,
    rw_paths: list[Path | str] | None = None,
    ro_paths: list[Path | str] | None = None,
    python_runtime: bool = False,
) -> subprocess.CompletedProcess[Any]:
    """Run a cancellation-safe sandboxed subprocess under the global limit with an end-to-end deadline."""
    start_time = asyncio.get_running_loop().time()
    async with asyncio.timeout(timeout):
        async with subprocess_guard():
            elapsed = asyncio.get_running_loop().time() - start_time
            remaining_timeout = max(1.0, timeout - elapsed)
            result = await async_sandboxed_run(
                cmd,
                timeout=int(remaining_timeout),
                rw_paths=rw_paths,
                ro_paths=ro_paths,
                python_runtime=python_runtime,
            )
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            result.args,
            output=result.stdout,
            stderr=result.stderr,
        )
    return result
