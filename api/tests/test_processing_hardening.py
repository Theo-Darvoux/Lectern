"""Regression tests for processing-root and cancellation ownership hardening."""

from __future__ import annotations

import asyncio
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.core.security.async_utils import shielded_to_thread
from app.core.security.processing_paths import (
    make_processing_temp_dir,
    make_processing_temp_path,
    validate_processing_path,
)


@pytest.fixture
def processing_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "processing"
    monkeypatch.setattr(settings, "processing_root", str(root))
    return root


def test_processing_temp_paths_are_private_descendants(processing_root: Path) -> None:
    temp_file = make_processing_temp_path(suffix=".bin")
    temp_dir = make_processing_temp_dir(prefix="job-")
    try:
        assert temp_file.parent == processing_root.resolve()
        assert temp_dir.parent == processing_root.resolve()
        assert temp_file.stat().st_mode & 0o777 == 0o600
        assert temp_dir.stat().st_mode & 0o777 == 0o700
        assert validate_processing_path(temp_file) == temp_file.resolve()
        assert validate_processing_path(temp_dir) == temp_dir.resolve()
    finally:
        temp_file.unlink(missing_ok=True)
        temp_dir.rmdir()


def test_processing_root_rejects_symlink(processing_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = processing_root.parent / "real"
    target.mkdir()
    link = processing_root.parent / "linked"
    link.symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(settings, "processing_root", str(link))

    with pytest.raises(RuntimeError, match="symbolic link"):
        make_processing_temp_path()


def test_processing_path_rejects_symlink_child(processing_root: Path) -> None:
    real = make_processing_temp_path()
    linked = processing_root / "linked-input"
    linked.symlink_to(real)
    try:
        with pytest.raises(RuntimeError, match="symbolic link"):
            validate_processing_path(linked)
    finally:
        linked.unlink(missing_ok=True)
        real.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_shielded_thread_survives_repeated_cancellation() -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def worker() -> int:
        started.set()
        release.wait(timeout=5)
        finished.set()
        return 42

    task = asyncio.create_task(shielded_to_thread(worker))
    await asyncio.to_thread(started.wait, 2)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()

    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


def test_sandbox_rejects_paths_outside_processing_root(
    processing_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.security import sandbox

    allowed = make_processing_temp_path()
    outside = tmp_path / "outside"
    outside.write_bytes(b"x")
    monkeypatch.setattr(sandbox, "_resolve_bwrap", lambda: "/usr/bin/bwrap")
    monkeypatch.setattr(sandbox, "_resolve_prlimit", lambda: "/usr/bin/prlimit")
    try:
        sandbox._sandbox_command(["true"], ro_paths=[allowed])
        with pytest.raises(ValueError, match="outside processing root"):
            sandbox._sandbox_command(["true"], ro_paths=[outside])
        with pytest.raises(ValueError, match="processing root itself"):
            sandbox._sandbox_command(["true"], ro_paths=[processing_root])
    finally:
        allowed.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_compression_stage_cleans_unadopted_output_on_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("redis")
    pytest.importorskip("pikepdf")
    pytest.importorskip("oletools")

    from app.core.security.file_security.compress import CompressResultPath
    from app.workers.upload.stages import compress as stage

    original = tmp_path / "original.bin"
    generated = tmp_path / "generated.bin"
    original.write_bytes(b"original")
    generated.write_bytes(b"generated")

    pf = type("PF", (), {"path": original})()
    pf.replace_with = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr(
        stage,
        "compress_file_path",
        AsyncMock(return_value=CompressResultPath(generated, 9, None, "application/octet-stream")),
    )

    @contextmanager
    def span(*_args: object, **_kwargs: object):
        yield None

    tracer = type("Tracer", (), {"start_as_current_span": span})()
    with pytest.raises(asyncio.CancelledError):
        await stage.run_compress_stage(
            pf, "application/octet-stream", "file.bin", tracer
        )
    assert not generated.exists()


@pytest.mark.asyncio
async def test_compression_dispatcher_preserves_sanitization_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("redis")
    pytest.importorskip("pikepdf")
    pytest.importorskip("oletools")

    from app.core.security.file_security import compress as dispatcher
    from app.core.security.file_security.errors import SanitizationError

    source = tmp_path / "large.png"
    source.write_bytes(b"x" * (dispatcher._COMPRESSION_SKIP_THRESHOLD + 1))
    monkeypatch.setattr(
        dispatcher,
        "_compress_image_path",
        lambda _path: (_ for _ in ()).throw(SanitizationError("unsafe image")),
    )

    with pytest.raises(SanitizationError, match="unsafe image"):
        await dispatcher.compress_file_path(source, "image/png", "large.png")


@pytest.mark.asyncio
async def test_async_sandbox_cancellation_during_spawn_reaps_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.security import sandbox

    class BlockingReader:
        async def read(self, _size: int) -> bytes:
            await asyncio.Event().wait()
            return b""

    class FakeProcess:
        def __init__(self) -> None:
            self.pid = 424242
            self.stdout = BlockingReader()
            self.stderr = BlockingReader()
            self.returncode = -9
            self.killed = False
            self.waited = False
            self._exited = asyncio.Event()

        def kill(self) -> None:
            self.killed = True
            self._exited.set()

        async def wait(self) -> int:
            await self._exited.wait()
            self.waited = True
            return self.returncode

    process = FakeProcess()
    spawn_allowed = asyncio.Event()

    async def fake_spawn(*_args: object, **_kwargs: object) -> FakeProcess:
        await spawn_allowed.wait()
        return process

    monkeypatch.setattr(sandbox, "_sandbox_command", lambda command, **_kwargs: command)
    monkeypatch.setattr(sandbox.asyncio, "create_subprocess_exec", fake_spawn)
    monkeypatch.setattr(sandbox.os, "killpg", lambda *_args: None)

    task = asyncio.create_task(sandbox.async_sandboxed_run(["fake"]))
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    assert not task.done()

    spawn_allowed.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert process.killed
    assert process.waited


@pytest.mark.asyncio
async def test_async_sandbox_spawn_timeout_reaps_late_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.security import sandbox

    class BlockingReader:
        async def read(self, _size: int) -> bytes:
            await asyncio.Event().wait()
            return b""

    class FakeProcess:
        def __init__(self) -> None:
            self.pid = 424243
            self.stdout = BlockingReader()
            self.stderr = BlockingReader()
            self.returncode = -9
            self.killed = False
            self.waited = False
            self._exited = asyncio.Event()

        def kill(self) -> None:
            self.killed = True
            self._exited.set()

        async def wait(self) -> int:
            await self._exited.wait()
            self.waited = True
            return self.returncode

    process = FakeProcess()
    spawn_allowed = asyncio.Event()

    async def fake_spawn(*_args: object, **_kwargs: object) -> FakeProcess:
        await spawn_allowed.wait()
        return process

    async def allow_late_spawn() -> None:
        await asyncio.sleep(0.05)
        spawn_allowed.set()

    monkeypatch.setattr(sandbox, "_sandbox_command", lambda command, **_kwargs: command)
    monkeypatch.setattr(sandbox.asyncio, "create_subprocess_exec", fake_spawn)
    monkeypatch.setattr(sandbox.os, "killpg", lambda *_args: None)

    releaser = asyncio.create_task(allow_late_spawn())
    with pytest.raises(TimeoutError, match="during creation"):
        await sandbox.async_sandboxed_run(["fake"], timeout=0.01)
    await releaser

    assert process.killed
    assert process.waited
