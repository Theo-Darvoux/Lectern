from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "promote-release-image.sh"


def _fake_docker(tmp_path: Path) -> tuple[Path, Path]:
    log = tmp_path / "docker.jsonl"
    executable = tmp_path / "docker"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "with open(os.environ['DOCKER_LOG'], 'a', encoding='utf-8') as stream:\n"
        "    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable, log


def test_promotion_preserves_exact_docker_argument_boundaries(tmp_path: Path) -> None:
    _, log = _fake_docker(tmp_path)
    sha = "a" * 40
    digest = "c" * 64
    env = os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "DOCKER_LOG": str(log),
        "GITHUB_SHA": sha,
    }
    subprocess.run(
        [
            str(SCRIPT),
            f"ghcr.io/theo-darvoux/lectern/api-candidate@sha256:{digest}",
            "ghcr.io/theo-darvoux/lectern/api-release",
            f"sha-{sha}",
            "latest",
        ],
        check=True,
        env=env,
    )
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert calls == [
        [
            "buildx",
            "imagetools",
            "create",
            "--tag",
            f"ghcr.io/theo-darvoux/lectern/api-release:sha-{sha}",
            f"ghcr.io/theo-darvoux/lectern/api-candidate@sha256:{digest}",
        ],
        [
            "buildx",
            "imagetools",
            "create",
            "--tag",
            "ghcr.io/theo-darvoux/lectern/api-release:latest",
            f"ghcr.io/theo-darvoux/lectern/api-release:sha-{sha}",
        ],
    ]


def test_promotion_rejects_cross_component_repository_copy(tmp_path: Path) -> None:
    _, log = _fake_docker(tmp_path)
    sha = "b" * 40
    digest = "d" * 64
    env = os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "DOCKER_LOG": str(log),
        "GITHUB_SHA": sha,
    }
    result = subprocess.run(
        [
            str(SCRIPT),
            f"ghcr.io/theo-darvoux/lectern/api-candidate@sha256:{digest}",
            "ghcr.io/theo-darvoux/lectern/web-release",
            f"sha-{sha}",
        ],
        check=False,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 64
    assert not log.exists()
