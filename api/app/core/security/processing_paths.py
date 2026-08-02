"""Private temporary-path management for untrusted file processing."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.config import settings


def _absolute_configured_root() -> Path:
    raw = Path(settings.processing_root).expanduser()
    if not raw.is_absolute():
        raise RuntimeError("PROCESSING_ROOT must be an absolute path")
    return raw.absolute()


def _reject_symlink_components(path: Path) -> None:
    """Reject any existing symlink in *path*, including the final component."""

    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise RuntimeError(f"Processing path contains a symbolic link: {current}")


def get_processing_root() -> Path:
    """Create and return the private, non-symlink processing root."""

    root = _absolute_configured_root()
    _reject_symlink_components(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    _reject_symlink_components(root)
    if not root.is_dir():
        raise RuntimeError(f"Configured processing root is not a directory: {root}")
    return root.resolve(strict=True)


def validate_processing_path(
    path: Path | str,
    *,
    allow_root: bool = False,
    require_exists: bool = True,
) -> Path:
    """Validate a path as a non-symlink descendant of the processing root."""

    root = get_processing_root()
    candidate = Path(path).expanduser().absolute()
    if candidate == root:
        if not allow_root:
            raise ValueError("Using the processing root itself is prohibited")
    elif not candidate.is_relative_to(root):
        raise ValueError(f"Path is outside processing root: {candidate}")

    _reject_symlink_components(candidate)
    try:
        resolved = candidate.resolve(strict=require_exists)
    except FileNotFoundError:
        raise ValueError(f"Processing path does not exist: {candidate}") from None
    if resolved == root and not allow_root:
        raise ValueError("Using the processing root itself is prohibited")
    if resolved != root and not resolved.is_relative_to(root):
        raise ValueError(f"Path resolves outside processing root: {candidate}")
    return resolved


def make_processing_temp_path(*, suffix: str = "", prefix: str = "tmp-") -> Path:
    """Create a closed mode-0600 temporary file beneath the processing root."""

    handle = tempfile.NamedTemporaryFile(
        suffix=suffix,
        prefix=prefix,
        dir=get_processing_root(),
        delete=False,
    )
    try:
        os.fchmod(handle.fileno(), 0o600)
        return Path(handle.name)
    finally:
        handle.close()


def make_processing_temp_dir(*, prefix: str = "tmp-") -> Path:
    """Create a mode-0700 temporary directory beneath the processing root."""

    path = Path(tempfile.mkdtemp(prefix=prefix, dir=get_processing_root()))
    path.chmod(0o700)
    return path


@contextmanager
def processing_temp_dir(*, prefix: str = "tmp-") -> Iterator[Path]:
    """Create and reliably remove a private processing directory."""

    path = make_processing_temp_dir(prefix=prefix)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
