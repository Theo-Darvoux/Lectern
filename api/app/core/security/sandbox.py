"""Sandboxed subprocess execution using Bubblewrap (bwrap).

Sandbox policy:
  - New PID / network / IPC / UTS / cgroup namespaces
  - Read-only system runtime mounts
  - Extra binds restricted to a configured processing root
  - No network access
  - Process-group termination and bounded stdout/stderr capture
  - POSIX limits applied by the external ``prlimit`` launcher
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import logging
import os
import shutil
import signal
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_bwrap_path: str | None = None
_bwrap_checked = False
_prlimit_path: str | None = None
_prlimit_checked = False

_MAX_SUBPROCESS_OUTPUT_BYTES = 10 * 1024 * 1024
_CLEANUP_TIMEOUT_SECONDS = 2.0


class SubprocessOutputLimitError(RuntimeError):
    """Raised when subprocess stdout or stderr exceeds the capture limit."""


def _resolve_bwrap() -> str:
    """Return the Bubblewrap executable path or fail closed."""
    global _bwrap_path, _bwrap_checked
    if not _bwrap_checked:
        _bwrap_path = shutil.which("bwrap")
        _bwrap_checked = True
    if _bwrap_path is None:
        raise RuntimeError(
            "bwrap (bubblewrap) is required for subprocess sandboxing but was not found."
        )
    return _bwrap_path


def _resolve_prlimit() -> str:
    """Return the external resource-limit launcher path or fail closed."""
    global _prlimit_path, _prlimit_checked
    if not _prlimit_checked:
        _prlimit_path = shutil.which("prlimit")
        _prlimit_checked = True
    if _prlimit_path is None:
        raise RuntimeError("prlimit is required for sandbox resource limits but was not found.")
    return _prlimit_path


_SYSTEM_RO_BINDS: tuple[str, ...] = (
    "/usr",
    "/lib",
    "/lib64",
    "/bin",
    "/sbin",
    "/etc/alternatives",
    "/etc/fonts",
    "/etc/ghostscript",
)


def _sandbox_environment() -> dict[str, str]:
    """Return the minimal non-secret environment exposed to processors."""
    return {
        "HOME": "/tmp",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
    }


def _overlaps(a: Path, b: Path) -> bool:
    """Return whether either resolved path contains the other."""
    return a == b or a.is_relative_to(b) or b.is_relative_to(a)


def _processing_root() -> Path:
    from app.core.security.processing_paths import get_processing_root

    return get_processing_root()


def _validate_bind_path(path: Path | str, *, processing_root: Path) -> Path:
    """Resolve a bind path and require a non-symlink child of the processing root."""
    from app.core.security.processing_paths import validate_processing_path

    resolved = validate_processing_path(path)
    if not resolved.is_relative_to(processing_root):
        raise ValueError(f"Sandbox path is outside processing root: {resolved}")
    return resolved


def _resource_limit_prefix() -> list[str]:
    """Build a ``prlimit`` prefix without using unsafe Python ``preexec_fn``."""
    from app.config import settings

    memory_bytes = settings.sandbox_memory_limit_mb * 1024 * 1024
    file_bytes = settings.sandbox_file_size_limit_mb * 1024 * 1024
    return [
        _resolve_prlimit(),
        f"--as={memory_bytes}",
        f"--cpu={settings.sandbox_cpu_limit_seconds}",
        f"--fsize={file_bytes}",
        f"--nproc={settings.sandbox_process_limit}",
        "--core=0",
        "--",
    ]


def _sandbox_command(
    cmd: list[str],
    *,
    rw_paths: Sequence[Path | str] | None = None,
    ro_paths: Sequence[Path | str] | None = None,
) -> list[str]:
    """Build the resource-limited Bubblewrap command."""
    if not cmd:
        raise ValueError("Sandbox command must not be empty")

    processing_root = _processing_root()
    validated_ro = [
        _validate_bind_path(path, processing_root=processing_root) for path in (ro_paths or ())
    ]
    validated_rw = [
        _validate_bind_path(path, processing_root=processing_root) for path in (rw_paths or ())
    ]

    for ro_path in validated_ro:
        for rw_path in validated_rw:
            if _overlaps(ro_path, rw_path):
                raise ValueError(
                    "Conflicting read-only and read-write sandbox bind path overlap: "
                    f"'{ro_path}' vs '{rw_path}'"
                )

    bwrap_cmd = [_resolve_bwrap()]
    if Path("/.dockerenv").exists():
        bwrap_cmd.extend(
            [
                "--unshare-user",
                "--unshare-pid",
                "--unshare-ipc",
                "--unshare-net",
                "--unshare-uts",
                "--unshare-cgroup-try",
            ]
        )
    else:
        bwrap_cmd.append("--unshare-all")

    bwrap_cmd.extend(["--die-with-parent", "--new-session", "--dev", "/dev", "--chdir", "/tmp"])
    if Path("/.dockerenv").exists():
        bwrap_cmd.extend(["--size", "104857600", "--tmpfs", "/proc"])
    else:
        bwrap_cmd.extend(["--proc", "/proc"])
    bwrap_cmd.extend(["--size", "104857600", "--tmpfs", "/tmp"])

    for system_path in _SYSTEM_RO_BINDS:
        if Path(system_path).exists():
            bwrap_cmd.extend(["--ro-bind", system_path, system_path])
    for path in dict.fromkeys(validated_ro):
        bwrap_cmd.extend(["--ro-bind", str(path), str(path)])
    for path in dict.fromkeys(validated_rw):
        bwrap_cmd.extend(["--bind", str(path), str(path)])

    return [*bwrap_cmd, "--", *_resource_limit_prefix(), *cmd]


def _read_sync_bounded_stream(stream: Any, max_bytes: int) -> bytes:
    """Read a synchronous stream incrementally and enforce a memory bound."""
    if stream is None:
        return b""
    buffer = bytearray()
    try:
        while chunk := stream.read(65536):
            buffer.extend(chunk)
            if len(buffer) > max_bytes:
                raise SubprocessOutputLimitError(
                    f"Subprocess output limit exceeded ({max_bytes:,} bytes)"
                )
        return bytes(buffer)
    finally:
        with contextlib.suppress(Exception):
            stream.close()


def _kill_and_reap_sync(proc: subprocess.Popen[bytes], pgid: int) -> None:
    """Best-effort process-group termination with bounded reaping."""
    with contextlib.suppress(ProcessLookupError, OSError):
        os.killpg(pgid, signal.SIGKILL)
    with contextlib.suppress(ProcessLookupError, OSError):
        proc.kill()
    try:
        proc.wait(timeout=_CLEANUP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        logger.error("Failed to reap sandboxed process %s after SIGKILL", proc.pid)


def sandboxed_run(
    cmd: list[str],
    *,
    rw_paths: Sequence[Path | str] | None = None,
    ro_paths: Sequence[Path | str] | None = None,
    timeout: int = 60,
    capture_output: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    """Run a sandboxed process with one end-to-end deadline."""
    wrapped = _sandbox_command(cmd, rw_paths=rw_paths, ro_paths=ro_paths)
    deadline = time.monotonic() + timeout

    def remaining_timeout() -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(wrapped, timeout)
        return remaining

    proc = subprocess.Popen(
        wrapped,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture_output else subprocess.DEVNULL,
        stderr=subprocess.PIPE if capture_output else subprocess.DEVNULL,
        env=_sandbox_environment(),
        start_new_session=True,
    )
    pgid = proc.pid

    if not capture_output:
        try:
            proc.wait(timeout=remaining_timeout())
        except BaseException:
            _kill_and_reap_sync(proc, pgid)
            raise
        return subprocess.CompletedProcess(wrapped, proc.returncode or 0, b"", b"")

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    stdout_future = executor.submit(
        _read_sync_bounded_stream, proc.stdout, _MAX_SUBPROCESS_OUTPUT_BYTES
    )
    stderr_future = executor.submit(
        _read_sync_bounded_stream, proc.stderr, _MAX_SUBPROCESS_OUTPUT_BYTES
    )
    try:
        done, pending = concurrent.futures.wait(
            [stdout_future, stderr_future],
            timeout=remaining_timeout(),
            return_when=concurrent.futures.FIRST_EXCEPTION,
        )
        for future in done:
            exception = future.exception()
            if exception is not None:
                raise exception

        if pending:
            done, still_pending = concurrent.futures.wait(
                pending,
                timeout=remaining_timeout(),
            )
            for future in done:
                exception = future.exception()
                if exception is not None:
                    raise exception
            if still_pending:
                raise subprocess.TimeoutExpired(wrapped, timeout)

        stdout = stdout_future.result()
        stderr = stderr_future.result()
        proc.wait(timeout=remaining_timeout())
    except BaseException:
        _kill_and_reap_sync(proc, pgid)
        raise
    finally:
        if proc.stdout is not None:
            with contextlib.suppress(Exception):
                proc.stdout.close()
        if proc.stderr is not None:
            with contextlib.suppress(Exception):
                proc.stderr.close()
        executor.shutdown(wait=False, cancel_futures=True)

    return subprocess.CompletedProcess(wrapped, proc.returncode or 0, stdout, stderr)


async def _read_bounded_stream(
    stream: asyncio.StreamReader | None,
    max_bytes: int,
) -> bytes:
    """Read an asynchronous stream incrementally and enforce a memory bound."""
    if stream is None:
        return b""
    buffer = bytearray()
    while chunk := await stream.read(65536):
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            raise SubprocessOutputLimitError(
                f"Subprocess output limit exceeded ({max_bytes:,} bytes)"
            )
    return bytes(buffer)


async def _terminate_and_reap(
    process: asyncio.subprocess.Process,
    pgid: int,
    stdout_task: asyncio.Task[bytes],
    stderr_task: asyncio.Task[bytes],
) -> None:
    """Kill, drain readers, and reap under one bounded cleanup deadline."""

    async def _cleanup() -> None:
        with contextlib.suppress(ProcessLookupError, OSError):
            os.killpg(pgid, signal.SIGKILL)
        with contextlib.suppress(ProcessLookupError, OSError):
            process.kill()

        for task in (stdout_task, stderr_task):
            task.cancel()
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        await process.wait()

    try:
        async with asyncio.timeout(_CLEANUP_TIMEOUT_SECONDS):
            await _cleanup()
    except TimeoutError:
        logger.error(
            "Timed out cleaning sandboxed process %s and its stream readers",
            process.pid,
        )
        for task in (stdout_task, stderr_task):
            task.cancel()
        with contextlib.suppress(ProcessLookupError, OSError):
            os.killpg(pgid, signal.SIGKILL)
        with contextlib.suppress(ProcessLookupError, OSError):
            process.kill()


async def async_sandboxed_run(
    cmd: list[str],
    *,
    rw_paths: Sequence[Path | str] | None = None,
    ro_paths: Sequence[Path | str] | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess[bytes]:
    """Run and reliably reap a sandboxed process under one deadline."""
    wrapped = _sandbox_command(cmd, rw_paths=rw_paths, ro_paths=ro_paths)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    spawn_task = asyncio.create_task(
        asyncio.create_subprocess_exec(
            *wrapped,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_sandbox_environment(),
            start_new_session=True,
        )
    )
    spawn_cancellation: asyncio.CancelledError | None = None
    spawn_timed_out = False

    while not spawn_task.done():
        try:
            remaining = deadline - loop.time()
            if remaining <= 0:
                spawn_timed_out = True
                await asyncio.shield(spawn_task)
            else:
                await asyncio.wait_for(asyncio.shield(spawn_task), timeout=remaining)
        except TimeoutError:
            # Do not abandon process creation: wait for the handle, then kill it.
            spawn_timed_out = True
        except asyncio.CancelledError as exc:
            spawn_cancellation = spawn_cancellation or exc

    try:
        process = spawn_task.result()
    except BaseException:
        if spawn_cancellation is not None:
            logger.exception("Sandbox process creation failed after caller cancellation")
            raise spawn_cancellation
        raise

    pgid = process.pid
    stdout_task = asyncio.create_task(
        _read_bounded_stream(process.stdout, _MAX_SUBPROCESS_OUTPUT_BYTES)
    )
    stderr_task = asyncio.create_task(
        _read_bounded_stream(process.stderr, _MAX_SUBPROCESS_OUTPUT_BYTES)
    )

    async def _cleanup_process() -> None:
        cleanup_task = asyncio.create_task(
            _terminate_and_reap(process, pgid, stdout_task, stderr_task)
        )
        while not cleanup_task.done():
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        try:
            cleanup_task.result()
        except Exception:
            logger.exception("Subprocess cleanup failed")

    if spawn_cancellation is not None or spawn_timed_out:
        await _cleanup_process()
        if spawn_cancellation is not None:
            raise spawn_cancellation
        raise TimeoutError(f"Sandbox process exceeded timeout of {timeout}s during creation")

    remaining = deadline - loop.time()
    if remaining <= 0:
        await _cleanup_process()
        raise TimeoutError(f"Sandbox process exceeded timeout of {timeout}s")

    try:
        async with asyncio.timeout(remaining):
            stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
            await process.wait()
    except BaseException:
        await _cleanup_process()
        raise

    return subprocess.CompletedProcess(wrapped, process.returncode or 0, stdout, stderr)
