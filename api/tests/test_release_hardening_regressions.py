from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
SANITIZE = SCRIPTS / "sanitize-production-images.py"
RESOLVE_SEAWEED = SCRIPTS / "resolve-seaweedfs-image.sh"
REQUIRE_TESTED_SEAWEED = SCRIPTS / "require-tested-seaweedfs-image.sh"
VALIDATE_COMPOSE = SCRIPTS / "validate-production-compose.py"
COMMIT = "a" * 40
DIGEST = "1" * 64


def _reference(repository: str, digest: str = DIGEST) -> str:
    return f"{repository}@sha256:{digest}"


def _release_values(profiles: str) -> dict[str, str]:
    values = {
        "RELEASE_COMMIT": COMMIT,
        "COMPOSE_PROFILES": profiles,
        "API_IMAGE": _reference("ghcr.io/theo-darvoux/lectern/api-release"),
        "WORKER_IMAGE": _reference("ghcr.io/theo-darvoux/lectern/worker-release"),
        "WEB_IMAGE": _reference("ghcr.io/theo-darvoux/lectern/web-release"),
        "POLICY_IMAGE_DIGEST": f"sha256:{DIGEST}",
        "REDIS_IMAGE": _reference("docker.io/library/redis"),
        "NGINX_IMAGE": _reference("docker.io/library/nginx"),
        "MEILI_IMAGE": _reference("docker.io/getmeili/meilisearch"),
        "EUROOFFICE_IMAGE": _reference("ghcr.io/euro-office/documentserver"),
    }
    enabled = set(filter(None, profiles.split(",")))
    if "postgres" in enabled:
        values["POSTGRES_IMAGE"] = _reference("docker.io/library/postgres")
    if "seaweedfs-prod" in enabled:
        values["SEAWEEDFS_IMAGE"] = _reference("docker.io/chrislusf/seaweedfs")
    if "selfhost-worker" in enabled:
        values["SELFHOST_WORKER_IMAGE"] = _reference(
            "ghcr.io/theo-darvoux/lectern/selfhost-worker-release"
        )
    return values


def _write_env(path: Path, values: dict[str, str]) -> None:
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")


@pytest.mark.parametrize(
    "profiles",
    [
        "",
        "postgres",
        "seaweedfs-prod",
        "selfhost-worker",
        "postgres,seaweedfs-prod",
        "postgres,selfhost-worker",
        "seaweedfs-prod,selfhost-worker",
        "postgres,seaweedfs-prod,selfhost-worker",
    ],
)
def test_sanitizer_supports_every_valid_production_profile_shape(
    tmp_path: Path, profiles: str
) -> None:
    env_file = tmp_path / "images.env"
    output = tmp_path / "canonical.env"
    _write_env(env_file, _release_values(profiles))
    result = subprocess.run(
        [
            sys.executable,
            str(SANITIZE),
            "--env-file",
            str(env_file),
            "--output",
            str(output),
            "--commit",
            COMMIT,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    canonical = output.read_text(encoding="utf-8")
    expected_profiles = ",".join(sorted(filter(None, profiles.split(","))))
    assert f"COMPOSE_PROFILES={expected_profiles}\n" in canonical
    for profile, key in (
        ("postgres", "POSTGRES_IMAGE="),
        ("seaweedfs-prod", "SEAWEEDFS_IMAGE="),
        ("selfhost-worker", "SELFHOST_WORKER_IMAGE="),
    ):
        assert (key in canonical) is (profile in set(filter(None, profiles.split(","))))


def test_zero_profile_file_still_rejects_profile_only_images(tmp_path: Path) -> None:
    values = _release_values("")
    values["SEAWEEDFS_IMAGE"] = _reference("docker.io/chrislusf/seaweedfs")
    env_file = tmp_path / "images.env"
    output = tmp_path / "canonical.env"
    _write_env(env_file, values)
    result = subprocess.run(
        [
            sys.executable,
            str(SANITIZE),
            "--env-file",
            str(env_file),
            "--output",
            str(output),
            "--commit",
            COMMIT,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "profile-only image variables are forbidden" in result.stderr


def test_seaweedfs_resolver_canonicalizes_short_repo_digest_output(tmp_path: Path) -> None:
    log = tmp_path / "docker.log"
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "with open(os.environ['DOCKER_LOG'], 'a', encoding='utf-8') as f:\n"
        "    f.write(' '.join(sys.argv[1:]) + '\\n')\n"
        f"print('chrislusf/seaweedfs@sha256:{DIGEST}')\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    env = os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "DOCKER_LOG": str(log),
    }
    result = subprocess.run(
        [str(RESOLVE_SEAWEED), "chrislusf/seaweedfs:4.29"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.stdout.strip() == f"docker.io/chrislusf/seaweedfs@sha256:{DIGEST}"
    invocation = log.read_text(encoding="utf-8")
    assert "buildx imagetools inspect" in invocation
    assert "docker pull" not in invocation
    assert "image inspect" not in invocation


def test_seaweedfs_approval_must_equal_tested_digest() -> None:
    tested = f"docker.io/chrislusf/seaweedfs@sha256:{'1' * 64}"
    approved = f"docker.io/chrislusf/seaweedfs@sha256:{'2' * 64}"
    result = subprocess.run(
        [str(REQUIRE_TESTED_SEAWEED), tested, approved],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 65
    assert "does not match the digest exercised by the live suites" in result.stderr


def test_finalizer_uses_tested_seaweedfs_and_validates_compose_before_artifact() -> None:
    build = (REPO_ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
    finalizer = build.split("  finalize-release:\n", 1)[1]
    assert "- resolve-seaweedfs-image" in finalizer
    assert "TESTED_SEAWEEDFS_IMAGE: ${{ needs.resolve-seaweedfs-image.outputs.image }}" in finalizer
    assert "require-tested-seaweedfs-image.sh" in finalizer
    assert "SEAWEEDFS_IMAGE=${TESTED_SEAWEEDFS_IMAGE}" in finalizer
    compose_index = finalizer.index(
        "Validate production Compose resolves exactly the certified images"
    )
    upload_index = finalizer.index("Publish authoritative release-complete artifact")
    assert compose_index < upload_index
    assert "validate-production-compose.py" in finalizer
    assert "production-compose-images.txt" in finalizer
    assert "production-compose.config.yml" in finalizer
    assert "env -i" in finalizer


def test_compose_validator_rejects_unexpected_image(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    images = tmp_path / "images.txt"
    expected = _reference("ghcr.io/theo-darvoux/lectern/api-release")
    manifest.write_text(json.dumps({"images": {"API_IMAGE": expected}}), encoding="utf-8")
    images.write_text(expected + "\n" + "docker.io/library/busybox@sha256:" + "2" * 64 + "\n")
    result = subprocess.run(
        [
            sys.executable,
            str(VALIDATE_COMPOSE),
            "--manifest",
            str(manifest),
            "--compose-images",
            str(images),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "unexpected:" in result.stderr


def test_sandbox_launcher_error_is_not_misreported_as_child_exit() -> None:
    from app.core.security.sandbox import (
        SandboxInfrastructureError,
        _raise_if_sandbox_launcher_failed,
    )

    with pytest.raises(SandboxInfrastructureError, match="bwrap"):
        _raise_if_sandbox_launcher_failed(1, b"bwrap: setting up uid map: Permission denied\n")
