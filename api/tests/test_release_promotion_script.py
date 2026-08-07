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
        "digest = 'sha256:' + 'c' * 64\n"
        "state = os.environ.get('DOCKER_STATE', '')\n"
        "if 'inspect' in args:\n"
        "    if os.environ.get('ALIAS_TEST') == '1':\n"
        "        print('{}')\n"
        "        raise SystemExit(0)\n"
        "    mode = os.environ.get('EXISTING_MODE', 'missing')\n"
        "    if mode == 'network':\n"
        "        print('connection refused', file=sys.stderr)\n"
        "        raise SystemExit(2)\n"
        "    if mode == 'conflict':\n"
        "        print('sha256:' + 'd' * 64)\n"
        "        raise SystemExit(0)\n"
        "    if mode == 'same' or (state and os.path.exists(state)):\n"
        "        print(digest)\n"
        "        raise SystemExit(0)\n"
        "    print('manifest unknown: not found', file=sys.stderr)\n"
        "    raise SystemExit(1)\n"
        "if '--metadata-file' in args:\n"
        "    path = args[args.index('--metadata-file') + 1]\n"
        "    with open(path, 'w', encoding='utf-8') as stream:\n"
        "        json.dump({'containerimage.descriptor': {'digest': digest}}, stream)\n"
        "    if state:\n"
        "        open(state, 'w', encoding='utf-8').close()\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return log


def _promotion_env(tmp_path: Path, log: Path, *, mode: str) -> dict[str, str]:
    return os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "DOCKER_LOG": str(log),
        "DOCKER_STATE": str(tmp_path / "promoted"),
        "EXISTING_MODE": mode,
        "GITHUB_SHA": "a" * 40,
    }


def _promotion_args() -> list[str]:
    sha = "a" * 40
    digest = "c" * 64
    return [
        str(PROMOTE),
        f"ghcr.io/theo-darvoux/lectern/api-candidate@sha256:{digest}",
        "ghcr.io/theo-darvoux/lectern/api-release",
        f"sha-{sha}",
    ]


def test_new_promotion_writes_commit_tag_once_and_verifies_it(tmp_path: Path) -> None:
    log = _fake_docker(tmp_path)
    result = subprocess.run(
        _promotion_args(),
        check=True,
        env=_promotion_env(tmp_path, log, mode="missing"),
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == f"sha256:{'c' * 64}"
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(calls) == 3
    assert calls[0][:3] == ["buildx", "imagetools", "inspect"]
    assert calls[1][:3] == ["buildx", "imagetools", "create"]
    assert calls[2][:3] == ["buildx", "imagetools", "inspect"]
    assert f"ghcr.io/theo-darvoux/lectern/api-release:sha-{'a' * 40}" in calls[1]
    assert "latest" not in " ".join(calls[1])


def test_same_digest_commit_tag_rerun_is_idempotent(tmp_path: Path) -> None:
    log = _fake_docker(tmp_path)
    result = subprocess.run(
        _promotion_args(),
        check=True,
        env=_promotion_env(tmp_path, log, mode="same"),
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == f"sha256:{'c' * 64}"
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(calls) == 1
    assert calls[0][:3] == ["buildx", "imagetools", "inspect"]


def test_conflicting_commit_tag_is_never_overwritten(tmp_path: Path) -> None:
    log = _fake_docker(tmp_path)
    result = subprocess.run(
        _promotion_args(),
        check=False,
        env=_promotion_env(tmp_path, log, mode="conflict"),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 65
    assert "already exists with a different digest" in result.stderr
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(calls) == 1
    assert "create" not in calls[0]


def test_existing_tag_lookup_network_failure_fails_closed(tmp_path: Path) -> None:
    log = _fake_docker(tmp_path)
    result = subprocess.run(
        _promotion_args(),
        check=False,
        env=_promotion_env(tmp_path, log, mode="network"),
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "could not safely determine" in result.stderr
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(calls) == 1


def test_promotion_rejects_alias_argument(tmp_path: Path) -> None:
    log = _fake_docker(tmp_path)
    result = subprocess.run(
        [*_promotion_args(), "latest"],
        check=False,
        env=_promotion_env(tmp_path, log, mode="missing"),
    )
    assert result.returncode == 64
    assert not log.exists()


def test_manual_alias_publisher_preflights_all_components_before_mutation(tmp_path: Path) -> None:
    log = _fake_docker(tmp_path)
    refs = [
        f"ghcr.io/theo-darvoux/lectern/{component}-release@sha256:{'c' * 64}"
        for component in ("api", "worker", "web", "selfhost-worker")
    ]
    env = os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "DOCKER_LOG": str(log),
        "ALIAS_TEST": "1",
    }
    subprocess.run([str(ALIASES), "latest", *refs], check=True, env=env)
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(calls) == 8
    assert all(call[:3] == ["buildx", "imagetools", "inspect"] for call in calls[:4])
    assert all(call[:3] == ["buildx", "imagetools", "create"] for call in calls[4:])
