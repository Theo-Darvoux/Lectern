import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.config import settings
from app.core.security.sandbox import _resolve_bwrap, sandboxed_run

# ── Command Construction Unit Tests (Mocked) ────────────────────────────────


def test_resolve_bwrap_found():
    with patch("shutil.which", return_value="/usr/bin/bwrap"):
        with patch("app.core.security.sandbox._bwrap_checked", False):  # Reset cache
            assert _resolve_bwrap() == "/usr/bin/bwrap"


def test_resolve_bwrap_missing():
    with patch("shutil.which", return_value=None):
        with patch("app.core.security.sandbox._bwrap_checked", False):  # Reset cache
            with pytest.raises(RuntimeError, match=r"bwrap \(bubblewrap\) is required"):
                _resolve_bwrap()


def test_sandboxed_run_basic_command(tmp_path: Path):
    test_dir = tmp_path / "test"
    test_dir.mkdir(parents=True, exist_ok=True)
    # Mock external launchers and keep binds under an explicit processing root.
    with (
        patch.object(settings, "processing_root", str(tmp_path)),
        patch("app.core.security.sandbox._resolve_bwrap", return_value="/usr/bin/bwrap"),
        patch("app.core.security.sandbox._resolve_prlimit", return_value="/usr/bin/prlimit"),
    ):
        # Mock subprocess.Popen to avoid actual execution
        with patch("app.core.security.sandbox.subprocess.Popen") as mock_popen:
            proc = MagicMock()
            proc.returncode = 0
            proc.stdout.read.side_effect = [b"ffmpeg version 5.1", b""]
            proc.stderr.read.side_effect = [b"", b""]
            proc.wait.return_value = 0
            mock_popen.return_value = proc

            cmd = ["ffmpeg", "-version"]
            sandboxed_run(cmd, rw_paths=[test_dir], timeout=10)

            # Verify the bwrap command construction
            args, kwargs = mock_popen.call_args
            bwrap_cmd = args[0]

            assert bwrap_cmd[0] == "/usr/bin/bwrap"
            assert "--unshare-all" in bwrap_cmd or "--unshare-user" in bwrap_cmd

            if os.path.exists("/.dockerenv"):
                assert "--unshare-pid" in bwrap_cmd
                assert bwrap_cmd[bwrap_cmd.index("--tmpfs") + 1] in ("/proc", "/tmp")

            assert "--bind" in bwrap_cmd
            assert str(test_dir) in bwrap_cmd

            assert "/proc" in bwrap_cmd or "--proc" in bwrap_cmd

            assert "--" in bwrap_cmd
            assert bwrap_cmd[-2] == "ffmpeg"
            assert bwrap_cmd[-1] == "-version"


# ── Real Environment Smoke Tests (Functional) ───────────────────────────────


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bwrap not installed on this host")
def test_sandboxed_run_smoke_test(tmp_path: Path):
    """Wait! This test actually runs bwrap on the current host.

    This is intended to catch environment-specific failures like the 'mount proc'
    permission error we've seen in Docker.
    """
    # A trivial command that should always succeed in a working sandbox
    cmd = ["/bin/ls", "/"]

    # We provide a temp dir to test bind-mount functionality
    test_dir = tmp_path / "sandbox_test"
    test_dir.mkdir()
    (test_dir / "canary.txt").write_text("hello-sandbox")

    try:
        result = sandboxed_run(cmd, rw_paths=[test_dir], timeout=5)

        # If we got here, bwrap didn't crash or return error due to mount failures
        assert result.returncode == 0

    except (subprocess.CalledProcessError, RuntimeError, subprocess.SubprocessError) as exc:
        # If it's a TimeoutExpired we could skip, but if it's a PermissionError or
        # bwrap returning 1 with a stderr message, we want to see it clearly.
        pytest.fail(
            f"Sandbox smoke test failed! This usually indicates environment restrictions "
            f"(like Docker or Kernel settings blocking proc mounts). Error: {exc}"
        )


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bwrap not installed on this host")
def test_sandboxed_run_isolation_check():
    """Verify that the sandbox actually blocks something (e.g. network/IPC)."""
    # Trying to touch a file outside our allowed rw_paths should fail or be blocked
    # In bwrap's --unshare-all, the root filesystem is empty/minimal by default.
    # We'll just verify that 'ls /' returns a restricted view.

    result = sandboxed_run(["/bin/ls", "/"])
    output = result.stdout.decode()

    # In our sandbox.py, we only --ro-bind /usr, /lib, /bin, etc.
    # We do NOT bind the root '/' itself.
    # So 'ls /' in the sandbox should NOT see the host's root (like /home or /root).
    assert "home" not in output.split()
    assert "tmp" in output.split()  # We have --tmpfs /tmp
