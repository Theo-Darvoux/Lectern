"""Unit tests for app.core.security.sandbox — sandboxed subprocess execution."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.config import settings
from app.core.security.sandbox import _sandbox_command, _sandbox_environment


def _reset_bwrap_cache() -> None:
    """Reset both launcher-path caches between tests."""
    import app.core.security.sandbox as mod

    mod._bwrap_path = None
    mod._bwrap_checked = False
    mod._prlimit_path = None
    mod._prlimit_checked = False


def _mock_launcher_path(name: str) -> str:
    """Return distinct realistic paths for bwrap and prlimit."""
    return f"/usr/bin/{name}"


@patch("app.core.security.sandbox.shutil.which", side_effect=_mock_launcher_path)
def test_sandbox_command_wraps_with_bwrap(
    _mock_which: MagicMock,
    monkeypatch,
) -> None:
    """When bwrap is available, commands should be wrapped with bwrap."""
    _reset_bwrap_cache()

    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-cross-sandbox-boundary")
    cmd = _sandbox_command(["echo", "hello"])

    # The command should contain the bwrap binary
    assert "/usr/bin/bwrap" in cmd
    separator_idx = cmd.index("--")
    assert cmd[separator_idx + 1] == "/usr/bin/prlimit"
    # Must contain --unshare-all for namespace isolation
    assert "--unshare-all" in cmd
    # Must contain --die-with-parent to prevent orphans
    assert "--die-with-parent" in cmd
    # No --share-net (network must be blocked)
    assert "--share-net" not in cmd
    # The original command should appear after the last "--"
    separator_idx = len(cmd) - 1 - cmd[::-1].index("--")
    assert cmd[separator_idx + 1 :] == ["echo", "hello"]
    assert _sandbox_environment() == {
        "HOME": "/tmp",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
    }
    assert "AWS_SECRET_ACCESS_KEY" not in _sandbox_environment()
    _reset_bwrap_cache()


@patch("app.core.security.sandbox._running_in_container", return_value=True)
@patch("app.core.security.sandbox.shutil.which", side_effect=_mock_launcher_path)
def test_container_sandbox_exposes_only_safe_proc_version(
    _mock_which: MagicMock,
    _mock_container: MagicMock,
) -> None:
    """Converters get the static proc marker without access to worker processes."""
    _reset_bwrap_cache()

    command = _sandbox_command(["echo", "hello"])
    proc_index = command.index("/proc")
    assert command[proc_index - 3 : proc_index + 1] == [
        "--size",
        "104857600",
        "--tmpfs",
        "/proc",
    ]
    version_index = command.index("/proc/version")
    assert command[version_index - 1 : version_index + 2] == [
        "--ro-bind",
        "/proc/version",
        "/proc/version",
    ]
    assert ["--ro-bind", "/proc", "/proc"] not in [
        command[index : index + 3] for index in range(len(command) - 2)
    ]
    _reset_bwrap_cache()


@patch("app.core.security.sandbox.shutil.which", side_effect=_mock_launcher_path)
def test_python_runtime_mount_does_not_expose_the_project_root(
    _mock_which: MagicMock,
) -> None:
    _reset_bwrap_cache()

    command = _sandbox_command(
        ["python", "-m", "app.core.security.parser_child"], python_runtime=True
    )
    project_root = str(Path(__file__).resolve().parents[1])
    bind_triples = [
        tuple(command[index : index + 3])
        for index, value in enumerate(command)
        if value == "--ro-bind"
    ]
    assert ("--ro-bind", project_root, project_root) not in bind_triples
    assert (
        "--ro-bind",
        str(Path(__file__).resolve().parents[1] / "app"),
        "/opt/lectern-python/app",
    ) in bind_triples
    python_environment = _sandbox_environment(python_runtime=True)
    assert set(python_environment) == {
        "HOME",
        "LANG",
        "LC_ALL",
        "PATH",
        "PYTHONPATH",
        "LD_LIBRARY_PATH",
    }
    assert python_environment["PYTHONPATH"].split(":")[0] == "/opt/lectern-python"
    _reset_bwrap_cache()


@patch("app.core.security.sandbox.shutil.which", return_value=None)
def test_sandbox_command_raises_without_bwrap(
    _mock_which: MagicMock,
) -> None:
    """When bwrap is not found, building the command must fail (no unsandboxed fallback)."""
    _reset_bwrap_cache()

    with pytest.raises(RuntimeError, match="bwrap"):
        _sandbox_command(["echo", "fallback"])
    _reset_bwrap_cache()


@patch("app.core.security.sandbox.shutil.which", side_effect=_mock_launcher_path)
def test_sandbox_command_rw_paths(
    _mock_which: MagicMock,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """rw_paths should produce --bind arguments in the bwrap command."""
    _reset_bwrap_cache()
    monkeypatch.setattr(settings, "processing_root", str(tmp_path))

    rw_dir = tmp_path / "workdir"
    rw_dir.mkdir()

    cmd = _sandbox_command(["gs", "--version"], rw_paths=[rw_dir])
    # Find the --bind pair for our rw_path
    rw_str = str(rw_dir)
    bind_indices = [i for i, v in enumerate(cmd) if v == "--bind"]
    found = any(
        cmd[i + 1] == rw_str and cmd[i + 2] == rw_str for i in bind_indices if i + 2 < len(cmd)
    )
    assert found, f"Expected --bind {rw_str} {rw_str} in {cmd}"
    _reset_bwrap_cache()


@patch("app.core.security.sandbox.shutil.which", side_effect=_mock_launcher_path)
def test_sandbox_command_ro_paths(
    _mock_which: MagicMock,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """ro_paths should produce --ro-bind arguments in the bwrap command."""
    _reset_bwrap_cache()
    monkeypatch.setattr(settings, "processing_root", str(tmp_path))

    ro_dir = tmp_path / "readonly"
    ro_dir.mkdir()

    cmd = _sandbox_command(["ffmpeg", "-version"], ro_paths=[ro_dir])
    ro_str = str(ro_dir)
    ro_indices = [i for i, v in enumerate(cmd) if v == "--ro-bind"]
    found = any(
        cmd[i + 1] == ro_str and cmd[i + 2] == ro_str for i in ro_indices if i + 2 < len(cmd)
    )
    assert found, f"Expected --ro-bind {ro_str} {ro_str} in {cmd}"
    _reset_bwrap_cache()


@patch("app.core.security.sandbox.shutil.which", side_effect=_mock_launcher_path)
def test_sandbox_command_accepts_an_operation_specific_output_limit(
    _mock_which: MagicMock,
) -> None:
    """A parser may widen only its output-file limit without changing global policy."""
    _reset_bwrap_cache()

    command = _sandbox_command(["echo", "hello"], file_size_limit_bytes=512 * 1024 * 1024)
    assert "--fsize=536870912" in command
    assert f"--as={settings.sandbox_memory_limit_mb * 1024 * 1024}" in command
    _reset_bwrap_cache()
