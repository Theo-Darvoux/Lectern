from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts/write-production-release-manifest.py"
_DIGEST = "0" * 64
_COMMIT = "a" * 40


def _reference(repository: str) -> str:
    return f"{repository}@sha256:{_DIGEST}"


def _base_values() -> dict[str, str]:
    return {
        "RELEASE_COMMIT": _COMMIT,
        "COMPOSE_PROFILES": "postgres,seaweedfs-prod,selfhost-worker",
        "API_IMAGE": _reference("ghcr.io/theo-darvoux/lectern/api-release"),
        "WORKER_IMAGE": _reference("ghcr.io/theo-darvoux/lectern/worker-release"),
        "WEB_IMAGE": _reference("ghcr.io/theo-darvoux/lectern/web-release"),
        "SELFHOST_WORKER_IMAGE": _reference("ghcr.io/theo-darvoux/lectern/selfhost-worker-release"),
        "POLICY_IMAGE_DIGEST": f"sha256:{_DIGEST}",
        "POSTGRES_IMAGE": _reference("docker.io/library/postgres"),
        "REDIS_IMAGE": _reference("docker.io/library/redis"),
        "NGINX_IMAGE": _reference("docker.io/library/nginx"),
        "MEILI_IMAGE": _reference("docker.io/getmeili/meilisearch"),
        "EUROOFFICE_IMAGE": _reference("ghcr.io/euro-office/documentserver"),
        "SEAWEEDFS_IMAGE": _reference("docker.io/chrislusf/seaweedfs"),
    }


def _write_env(path: Path, values: dict[str, str]) -> None:
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()))


def _run(tmp_path: Path, values: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env_file = tmp_path / "images.env"
    output = tmp_path / "manifest.json"
    _write_env(env_file, values)
    return subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--env-file",
            str(env_file),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_manifest_records_every_enabled_profile_image(tmp_path: Path) -> None:
    values = _base_values()
    result = _run(tmp_path, values)
    assert result.returncode == 0, result.stderr
    payload = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert payload["release_commit"] == _COMMIT
    assert payload["compose_profiles"] == ["postgres", "seaweedfs-prod", "selfhost-worker"]
    expected_images = {
        key: value
        for key, value in sorted(values.items())
        if key.endswith("_IMAGE") or key == "POLICY_IMAGE_DIGEST"
    }
    assert payload["images"] == expected_images
    assert len(payload["source_env_sha256"]) == 64


def test_manifest_rejects_mutable_infrastructure_tag(tmp_path: Path) -> None:
    values = _base_values()
    values["REDIS_IMAGE"] = "redis:7-alpine"
    result = _run(tmp_path, values)
    assert result.returncode != 0
    assert "REDIS_IMAGE" in result.stderr
    assert not (tmp_path / "manifest.json").exists()


def test_profile_only_images_are_not_required_when_profiles_are_disabled(tmp_path: Path) -> None:
    values = _base_values()
    values["COMPOSE_PROFILES"] = ""
    for variable in ("POSTGRES_IMAGE", "SEAWEEDFS_IMAGE", "SELFHOST_WORKER_IMAGE"):
        values.pop(variable)
    result = _run(tmp_path, values)
    assert result.returncode == 0, result.stderr
    payload = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert "POSTGRES_IMAGE" not in payload["images"]
    assert "SEAWEEDFS_IMAGE" not in payload["images"]
    assert "SELFHOST_WORKER_IMAGE" not in payload["images"]


def test_manifest_requires_explicit_production_profiles_override(tmp_path: Path) -> None:
    values = _base_values()
    values.pop("COMPOSE_PROFILES")
    result = _run(tmp_path, values)
    assert result.returncode != 0
    assert "COMPOSE_PROFILES" in result.stderr
