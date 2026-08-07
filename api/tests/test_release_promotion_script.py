from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMOTE = REPO_ROOT / "scripts" / "promote-release-image.sh"
ALIASES = REPO_ROOT / "scripts" / "publish-release-aliases.sh"


def _fake_docker(tmp_path: Path) -> Path:
    log = tmp_path / "docker.jsonl"
    executable = tmp_path / "docker"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['DOCKER_LOG'], 'a', encoding='utf-8') as stream:\n"
        "    stream.write(json.dumps(args) + '\\n')\n"
        "if '--metadata-file' in args:\n"
        "    path = args[args.index('--metadata-file') + 1]\n"
        "    with open(path, 'w', encoding='utf-8') as stream:\n"
        "        json.dump({'containerimage.descriptor': {'digest': 'sha256:' + 'c' * 64}}, stream)\n"
        "if 'inspect' in args:\n"
        "    print(json.dumps({'digest': 'sha256:' + 'c' * 64, 'manifests': []}))\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return log


def test_promotion_publishes_only_immutable_tag_and_outputs_digest(tmp_path: Path) -> None:
    log = _fake_docker(tmp_path)
    sha = "a" * 40
    digest = "c" * 64
    env = os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "DOCKER_LOG": str(log),
        "GITHUB_SHA": sha,
    }
    result = subprocess.run(
        [
            str(PROMOTE),
            f"ghcr.io/theo-darvoux/lectern/api-candidate@sha256:{digest}",
            "ghcr.io/theo-darvoux/lectern/api-release",
            f"sha-{sha}",
        ],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == f"sha256:{digest}"
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(calls) == 1
    assert calls[0][:3] == ["buildx", "imagetools", "create"]
    assert "--metadata-file" in calls[0]
    assert "--tag" in calls[0]
    assert f"ghcr.io/theo-darvoux/lectern/api-release:sha-{sha}" in calls[0]
    assert "latest" not in " ".join(calls[0])


def test_promotion_rejects_alias_argument(tmp_path: Path) -> None:
    log = _fake_docker(tmp_path)
    sha = "a" * 40
    result = subprocess.run(
        [
            str(PROMOTE),
            f"ghcr.io/theo-darvoux/lectern/api-candidate@sha256:{'c' * 64}",
            "ghcr.io/theo-darvoux/lectern/api-release",
            f"sha-{sha}",
            "latest",
        ],
        check=False,
        env=os.environ | {"PATH": f"{tmp_path}:{os.environ['PATH']}", "DOCKER_LOG": str(log)},
    )
    assert result.returncode == 64
    assert not log.exists()


def test_alias_publisher_preflights_all_components_before_mutation(tmp_path: Path) -> None:
    log = _fake_docker(tmp_path)
    refs = [
        f"ghcr.io/theo-darvoux/lectern/{component}-release@sha256:{'c' * 64}"
        for component in ("api", "worker", "web", "selfhost-worker")
    ]
    subprocess.run(
        [str(ALIASES), "latest", *refs],
        check=True,
        env=os.environ | {"PATH": f"{tmp_path}:{os.environ['PATH']}", "DOCKER_LOG": str(log)},
    )
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(calls) == 8
    assert all(call[:3] == ["buildx", "imagetools", "inspect"] for call in calls[:4])
    assert all(call[:3] == ["buildx", "imagetools", "create"] for call in calls[4:])
