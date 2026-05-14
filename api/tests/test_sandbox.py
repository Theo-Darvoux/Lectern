"""Unit tests for app.core.sandbox — sandboxed subprocess execution."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.core.sandbox import sandboxed_run


def _reset_bwrap_cache() -> None:
    """Reset the module-level bwrap path cache between tests."""
    import app.core.sandbox as mod

    mod._bwrap_path = False


@patch("app.core.sandbox.subprocess.run")
@patch("app.core.sandbox.shutil.which", return_value="/usr/bin/bwrap")
def test_sandboxed_run_with_bwrap(
    _mock_which: MagicMock,
    mock_run: MagicMock,
) -> None:
    """When bwrap is available, commands should be wrapped with bwrap."""
    _reset_bwrap_cache()
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)

    result = sandboxed_run(["echo", "hello"], timeout=10)

    assert result.returncode == 0
    call_args = mock_run.call_args
    cmd = call_args[0][0]

    # The command should start with the bwrap binary
    assert cmd[0] == "/usr/bin/bwrap"
    # Must contain --unshare-all for namespace isolation
    assert "--unshare-all" in cmd
    # Must contain --die-with-parent to prevent orphans
    assert "--die-with-parent" in cmd
    # No --share-net (network must be blocked)
    assert "--share-net" not in cmd
    # The original command should appear after "--"
    separator_idx = cmd.index("--")
    assert cmd[separator_idx + 1 :] == ["echo", "hello"]
    _reset_bwrap_cache()


@patch("app.core.sandbox.subprocess.run")
@patch("app.core.sandbox.shutil.which", return_value=None)
def test_sandboxed_run_raises_without_bwrap(
    _mock_which: MagicMock,
    mock_run: MagicMock,
) -> None:
    """When bwrap is not found, sandboxed_run must raise RuntimeError (no fallback)."""
    import pytest

    _reset_bwrap_cache()

    with pytest.raises(RuntimeError, match="bwrap"):
        sandboxed_run(["echo", "fallback"], timeout=5)

    # subprocess.run should never be called
    mock_run.assert_not_called()
    _reset_bwrap_cache()


@patch("app.core.sandbox.subprocess.run")
@patch("app.core.sandbox.shutil.which", return_value="/usr/bin/bwrap")
def test_sandboxed_run_rw_paths(
    _mock_which: MagicMock,
    mock_run: MagicMock,
    tmp_path: Path,
) -> None:
    """rw_paths should produce --bind arguments in the bwrap command."""
    _reset_bwrap_cache()
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)

    rw_dir = tmp_path / "workdir"
    rw_dir.mkdir()

    sandboxed_run(["gs", "--version"], rw_paths=[rw_dir], timeout=5)

    cmd = mock_run.call_args[0][0]
    # Find the --bind pair for our rw_path
    rw_str = str(rw_dir)
    bind_indices = [i for i, v in enumerate(cmd) if v == "--bind"]
    found = any(
        cmd[i + 1] == rw_str and cmd[i + 2] == rw_str for i in bind_indices if i + 2 < len(cmd)
    )
    assert found, f"Expected --bind {rw_str} {rw_str} in {cmd}"
    _reset_bwrap_cache()


@patch("app.core.sandbox.subprocess.run")
@patch("app.core.sandbox.shutil.which", return_value="/usr/bin/bwrap")
def test_sandboxed_run_ro_paths(
    _mock_which: MagicMock,
    mock_run: MagicMock,
    tmp_path: Path,
) -> None:
    """ro_paths should produce --ro-bind arguments in the bwrap command."""
    _reset_bwrap_cache()
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)

    ro_dir = tmp_path / "readonly"
    ro_dir.mkdir()

    sandboxed_run(["ffmpeg", "-version"], ro_paths=[ro_dir], timeout=5)

    cmd = mock_run.call_args[0][0]
    ro_str = str(ro_dir)
    ro_indices = [i for i, v in enumerate(cmd) if v == "--ro-bind"]
    found = any(
        cmd[i + 1] == ro_str and cmd[i + 2] == ro_str for i in ro_indices if i + 2 < len(cmd)
    )
    assert found, f"Expected --ro-bind {ro_str} {ro_str} in {cmd}"
    _reset_bwrap_cache()


@patch("app.core.sandbox.shutil.which", return_value="/usr/bin/bwrap")
def test_sandboxed_run_timeout_propagates(
    _mock_which: MagicMock,
) -> None:
    """TimeoutExpired from subprocess should propagate through sandboxed_run."""
    _reset_bwrap_cache()
    import pytest

    with (
        patch(
            "app.core.sandbox.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["sleep"], timeout=1),
        ),
        pytest.raises(subprocess.TimeoutExpired),
    ):
        sandboxed_run(["sleep", "999"], timeout=1)
    _reset_bwrap_cache()
