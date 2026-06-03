"""Sandboxed subprocess execution using Bubblewrap (bwrap).

Provides a single ``sandboxed_run()`` function that wraps ``subprocess.run()``
with Linux namespace isolation.  Raises ``RuntimeError`` when ``bwrap`` is not
installed — there is no unsandboxed fallback.

Sandbox policy:
  - New PID / network / IPC / UTS / cgroup namespaces (``--unshare-all``)
  - Read-only bind mounts for system binaries and shared libraries
  - Read-write access **only** to explicitly listed paths (temp dirs)
  - No network access (no ``--share-net``)
  - ``--die-with-parent`` to prevent orphaned processes
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Bwrap availability detection (evaluated once) ───────────────────────────

_bwrap_path: str | None | bool = False  # False = not yet checked


def _resolve_bwrap() -> str:
    """Find bwrap in $PATH.  Result is cached after first call.

    Raises ``RuntimeError`` if bwrap is not installed.
    """
    global _bwrap_path
    if _bwrap_path is not False:
        return _bwrap_path  # type: ignore[return-value]

    found = shutil.which("bwrap")
    if found is None:
        raise RuntimeError(
            "bwrap (bubblewrap) is required for subprocess sandboxing but was not found."
        )
    _bwrap_path = found
    return found


# ── Read-only paths required by ffmpeg / ghostscript / exiftool ─────────────

_SYSTEM_RO_BINDS: list[str] = [
    "/usr",
    "/lib",
    "/lib64",
    "/bin",
    "/sbin",
    "/etc/alternatives",
    "/etc/fonts",
    "/etc/ghostscript",
]

# ── Public API ──────────────────────────────────────────────────────────────


def sandboxed_run(
    cmd: list[str],
    *,
    rw_paths: list[Path | str] | None = None,
    ro_paths: list[Path | str] | None = None,
    timeout: int = 60,
    capture_output: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    """Run *cmd* inside a bwrap sandbox.

    Args:
        cmd: Command and arguments (e.g. ``["ffmpeg", "-y", "-i", ...]``).
        rw_paths: Paths that the subprocess needs to read **and** write
                  (typically temp dirs containing input/output files).
        ro_paths: Extra paths the subprocess needs to read (beyond the
                  default system mounts).
        timeout: Maximum wall-clock seconds before ``TimeoutExpired``.
        capture_output: Capture stdout/stderr (default ``True``).

    Returns:
        ``subprocess.CompletedProcess``.

    Raises:
        RuntimeError: If bwrap is not installed.
    """
    bwrap = _resolve_bwrap()

    bwrap_cmd = [
        bwrap,
    ]

    if Path("/.dockerenv").exists():
        # In Docker unprivileged user namespaces cannot mount a new /proc.
        # We must avoid unsharing the PID namespace and just bind-mount the host's /proc.
        bwrap_cmd.extend(
            [
                "--unshare-user",
                "--unshare-ipc",
                "--unshare-net",
                "--unshare-uts",
                "--unshare-cgroup-try",
            ]
        )
    else:
        bwrap_cmd.append("--unshare-all")

    bwrap_cmd.extend(
        [
            "--die-with-parent",  # kill child if API dies
            "--new-session",  # detach from the controlling terminal
        ]
    )

    # Minimal virtual filesystems
    bwrap_cmd.extend(
        [
            "--dev",
            "/dev",
        ]
    )

    if Path("/.dockerenv").exists():
        bwrap_cmd.extend(["--ro-bind", "/proc", "/proc"])
    else:
        bwrap_cmd.extend(["--proc", "/proc"])

    bwrap_cmd.extend(
        [
            "--tmpfs",
            "/tmp",
        ]
    )

    # System read-only mounts (binaries, libs, config)
    for sys_path in _SYSTEM_RO_BINDS:
        if Path(sys_path).exists():
            bwrap_cmd.extend(["--ro-bind", sys_path, sys_path])

    # Caller-specified read-only mounts
    for ro_path in ro_paths or []:
        p = str(ro_path)
        bwrap_cmd.extend(["--ro-bind", p, p])

    # Caller-specified read-write mounts (temp dirs with input/output files)
    for rw_path in rw_paths or []:
        p = str(rw_path)
        bwrap_cmd.extend(["--bind", p, p])

    # Separator then the actual command
    bwrap_cmd.append("--")
    bwrap_cmd.extend(cmd)

    return subprocess.run(
        bwrap_cmd,
        capture_output=capture_output,
        timeout=timeout,
    )
