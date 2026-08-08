from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
TOOLCHAIN = REPO_ROOT / "deploy/release-toolchain.env"
WRITE_MANIFEST = SCRIPTS / "write-production-release-manifest.py"
PREPARE = SCRIPTS / "prepare-production-release.sh"

EXPECTED_SEAWEED = (
    "docker.io/chrislusf/seaweedfs@sha256:"
    "d47c7ee99fcb951351d7194915f4e3a5ea604a8e8871183d713907dec4fb9bf5"
)


def _parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw and not raw.startswith("#"):
            key, value = raw.split("=", 1)
            values[key] = value
    return values


def _step_window(text: str, needle: str, width: int = 12) -> list[list[str]]:
    lines = text.splitlines()
    windows: list[list[str]] = []
    for index, line in enumerate(lines):
        if needle in line:
            windows.append(lines[index : index + width])
    return windows


def test_release_toolchain_is_fully_immutable_and_reviewed() -> None:
    values = _parse_env(TOOLCHAIN)
    assert values == {
        "BUILDX_VERSION": "v0.36.1",
        "BUILDKIT_VERSION": "v0.32.2",
        "BUILDKIT_IMAGE": (
            "docker.io/moby/buildkit@sha256:"
            "040d34121c27906c4ff9ac152a30d52bf2c5d328d3bb748916bb3d2743c02528"
        ),
        "BINFMT_VERSION": "qemu-v10.2.3-68",
        "BINFMT_IMAGE": (
            "docker.io/tonistiigi/binfmt@sha256:"
            "465d3fdd28d0f2b871ba4b4ec98bd183292e96167f00d9fd40bd249f8632d705"
        ),
        "SEAWEEDFS_VERSION": "4.29",
        "SEAWEEDFS_TEST_IMAGE": EXPECTED_SEAWEED,
    }


def test_every_buildx_and_qemu_setup_consumes_repository_pins() -> None:
    workflows = [
        REPO_ROOT / ".github/workflows/build.yml",
        REPO_ROOT / ".github/workflows/ci.yml",
        REPO_ROOT / ".github/workflows/scan-and-promote.yml",
        REPO_ROOT / ".github/workflows/seaweedfs-integration.yml",
    ]
    buildx_count = 0
    qemu_count = 0
    for path in workflows:
        text = path.read_text(encoding="utf-8")
        assert "export-release-toolchain.py --github-output \"$GITHUB_OUTPUT\"" in text
        for window in _step_window(text, "docker/setup-buildx-action@"):
            block = "\n".join(window)
            buildx_count += 1
            assert "version: ${{ steps.toolchain.outputs.buildx_version }}" in block
            assert "image=${{ steps.toolchain.outputs.buildkit_image }}" in block
        for window in _step_window(text, "docker/setup-qemu-action@"):
            block = "\n".join(window)
            qemu_count += 1
            assert "image: ${{ steps.toolchain.outputs.binfmt_image }}" in block
            assert "platforms: arm64" in block
    assert buildx_count == 9
    assert qemu_count == 4


def test_required_ci_and_release_use_only_repo_pinned_seaweedfs_digest() -> None:
    for relative in (".github/workflows/ci.yml", ".github/workflows/build.yml"):
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert "chrislusf/seaweedfs:4.29" not in text
        assert "seaweedfs_test_image" in text
        assert "SEAWEEDFS_SOURCE_IMAGE" in text
    standalone = (REPO_ROOT / ".github/workflows/seaweedfs-integration.yml").read_text(
        encoding="utf-8"
    )
    assert "default: ''" in standalone
    assert "PINNED_SEAWEEDFS_IMAGE" in standalone


def test_local_preparation_accepts_canonical_manifest_not_image_authoring() -> None:
    script = PREPARE.read_text(encoding="utf-8")
    assert "--canonical-manifest" in script
    assert "--runtime-env" in script
    assert "materialize-production-deployment.py" in script
    assert "production-image-env-file" not in script
    assert "sanitize-production-images.py" not in script
    assert "config --quiet --no-env-resolution" in script
    assert "--format json --no-env-resolution" in script
    assert "production-compose.config.yml" not in script
    assert "production-compose-images.txt" not in script


def _release_values(commit: str) -> dict[str, str]:
    digest = "1" * 64
    return {
        "RELEASE_COMMIT": commit,
        "COMPOSE_PROFILES": "postgres,seaweedfs-prod,selfhost-worker",
        "API_IMAGE": f"ghcr.io/theo-darvoux/lectern/api-release@sha256:{digest}",
        "WORKER_IMAGE": f"ghcr.io/theo-darvoux/lectern/worker-release@sha256:{digest}",
        "WEB_IMAGE": f"ghcr.io/theo-darvoux/lectern/web-release@sha256:{digest}",
        "SELFHOST_WORKER_IMAGE": (
            f"ghcr.io/theo-darvoux/lectern/selfhost-worker-release@sha256:{digest}"
        ),
        "POLICY_IMAGE_DIGEST": f"sha256:{digest}",
        "POSTGRES_IMAGE": f"docker.io/library/postgres@sha256:{digest}",
        "REDIS_IMAGE": f"docker.io/library/redis@sha256:{digest}",
        "NGINX_IMAGE": f"docker.io/library/nginx@sha256:{digest}",
        "MEILI_IMAGE": f"docker.io/getmeili/meilisearch@sha256:{digest}",
        "EUROOFFICE_IMAGE": f"ghcr.io/euro-office/documentserver@sha256:{digest}",
        "SEAWEEDFS_IMAGE": EXPECTED_SEAWEED,
    }


def _canonical_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _inspection(commit: str, values: dict[str, str]) -> dict[str, object]:
    workload_repositories = {
        "API_IMAGE": "ghcr.io/theo-darvoux/lectern/api-release",
        "WORKER_IMAGE": "ghcr.io/theo-darvoux/lectern/worker-release",
        "WEB_IMAGE": "ghcr.io/theo-darvoux/lectern/web-release",
        "SELFHOST_WORKER_IMAGE": "ghcr.io/theo-darvoux/lectern/selfhost-worker-release",
    }
    records: dict[str, object] = {}
    for key, value in values.items():
        if key in {"RELEASE_COMMIT", "COMPOSE_PROFILES"}:
            continue
        reference = (
            f"docker.io/library/alpine@{value}" if key == "POLICY_IMAGE_DIGEST" else value
        )
        digest = reference.rsplit("@", 1)[1]
        repository = workload_repositories.get(key)
        records[key] = {
            "reference": reference,
            "digest": digest,
            "platforms": ["linux/amd64", "linux/arm64"] if repository else ["linux/amd64"],
            "commit_tag_reference": f"{repository}:sha-{commit}" if repository else None,
            "commit_tag_digest": digest if repository else None,
        }
    return {
        "schema_version": 1,
        "release_commit": commit,
        "required_workload_platforms": ["linux/amd64", "linux/arm64"],
        "images": records,
    }


def _service_images(values: dict[str, str]) -> dict[str, str]:
    policy = f"docker.io/library/alpine@{values['POLICY_IMAGE_DIGEST']}"
    services = {
        "release-image-policy": policy,
        "postgres-image-policy": policy,
        "selfhost-worker-image-policy": policy,
        "seaweedfs-image-policy": policy,
        "postgres": values["POSTGRES_IMAGE"],
        "redis": values["REDIS_IMAGE"],
        "meilisearch": values["MEILI_IMAGE"],
        "eurooffice": values["EUROOFFICE_IMAGE"],
        "api": values["API_IMAGE"],
        "worker": values["WORKER_IMAGE"],
        "worker-fast": values["WORKER_IMAGE"],
        "worker-slow": values["WORKER_IMAGE"],
        "web": values["WEB_IMAGE"],
        "selfhost-worker": values["SELFHOST_WORKER_IMAGE"],
        "nginx": values["NGINX_IMAGE"],
    }
    for service in (
        "seaweedfs-master",
        "seaweedfs-volume1",
        "seaweedfs-volume2",
        "seaweedfs-filer",
        "seaweedfs-s3",
    ):
        services[service] = values["SEAWEEDFS_IMAGE"]
    return dict(sorted(services.items()))


def _make_canonical_manifest(tmp_path: Path, commit: str) -> Path:
    values = _release_values(commit)
    env_file = tmp_path / "images.env"
    env_file.write_text(
        "".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8"
    )
    inspection_file = tmp_path / "inspection.json"
    _canonical_json(inspection_file, _inspection(commit, values))
    service_map_file = tmp_path / "services.json"
    _canonical_json(
        service_map_file,
        {
            "schema_version": 1,
            "release_commit": commit,
            "compose_profiles": ["postgres", "seaweedfs-prod", "selfhost-worker"],
            "release_input_sha256": hashlib.sha256(env_file.read_bytes()).hexdigest(),
            "services": _service_images(values),
        },
    )
    manifest = tmp_path / f"production-{commit}.json"
    result = subprocess.run(
        [
            sys.executable,
            str(WRITE_MANIFEST),
            "--env-file",
            str(env_file),
            "--inspection-file",
            str(inspection_file),
            "--compose-service-map-file",
            str(service_map_file),
            "--output",
            str(manifest),
            "--commit",
            commit,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return manifest


def _write_fake_docker(tmp_path: Path) -> Path:
    docker = tmp_path / "docker"
    docker.write_text(
        r'''#!/usr/bin/env python3
import json
import sys
from pathlib import Path

args = sys.argv[1:]
with Path(__file__).with_name("docker.log").open("a", encoding="utf-8") as stream:
    stream.write(" ".join(args) + "\n")

if args[:3] == ["buildx", "imagetools", "inspect"]:
    ref = args[3]
    if "@sha256:" in ref:
        digest = "sha256:" + ref.rsplit("@sha256:", 1)[1]
    else:
        digest = "sha256:" + "1" * 64
    is_workload = any(
        marker in ref
        for marker in ("api-release", "worker-release", "web-release", "selfhost-worker-release")
    )
    manifests = [{"platform": {"os": "linux", "architecture": "amd64"}}]
    if is_workload:
        manifests.append({"platform": {"os": "linux", "architecture": "arm64"}})
    print(json.dumps({"digest": digest, "manifests": manifests}))
    raise SystemExit(0)

if args and args[0] == "compose" and "config" in args:
    if "--quiet" in args:
        raise SystemExit(0)
    env_files = [Path(args[index + 1]) for index, value in enumerate(args) if value == "--env-file"]
    values = {}
    for raw in env_files[-1].read_text(encoding="utf-8").splitlines():
        if raw and not raw.startswith("#"):
            key, value = raw.split("=", 1)
            values[key] = value
    policy = "docker.io/library/alpine@" + values["POLICY_IMAGE_DIGEST"]
    services = {
        "release-image-policy": {"image": policy},
        "redis": {"image": values["REDIS_IMAGE"]},
        "meilisearch": {"image": values["MEILI_IMAGE"]},
        "eurooffice": {"image": values["EUROOFFICE_IMAGE"]},
        "api": {"image": values["API_IMAGE"]},
        "worker": {"image": values["WORKER_IMAGE"]},
        "worker-fast": {"image": values["WORKER_IMAGE"]},
        "worker-slow": {"image": values["WORKER_IMAGE"]},
        "web": {"image": values["WEB_IMAGE"]},
        "nginx": {"image": values["NGINX_IMAGE"]},
    }
    profiles = set(filter(None, values["COMPOSE_PROFILES"].split(",")))
    if "postgres" in profiles:
        services["postgres-image-policy"] = {"image": policy}
        services["postgres"] = {"image": values["POSTGRES_IMAGE"]}
    if "selfhost-worker" in profiles:
        services["selfhost-worker-image-policy"] = {"image": policy}
        services["selfhost-worker"] = {"image": values["SELFHOST_WORKER_IMAGE"]}
    if "seaweedfs-prod" in profiles:
        services["seaweedfs-image-policy"] = {"image": policy}
        for name in (
            "seaweedfs-master",
            "seaweedfs-volume1",
            "seaweedfs-volume2",
            "seaweedfs-filer",
            "seaweedfs-s3",
        ):
            services[name] = {"image": values["SEAWEEDFS_IMAGE"]}
    print(json.dumps({"services": services}))
    raise SystemExit(0)

print("unsupported fake docker invocation: " + " ".join(args), file=sys.stderr)
raise SystemExit(64)
''',
        encoding="utf-8",
    )
    docker.chmod(0o755)
    return docker


def test_local_preparation_never_persists_runtime_secret_sentinels(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    for relative in (
        "compose.yaml",
        "compose.prod.yaml",
        ".env.example",
        "deploy/release-toolchain.env",
        "scripts/release_manifest_lib.py",
        "scripts/materialize-production-deployment.py",
        "scripts/prepare-production-release.sh",
        "scripts/inspect-production-images.py",
        "scripts/validate-production-compose.py",
    ):
        source = REPO_ROOT / relative
        destination = checkout / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
        if relative.startswith("scripts/"):
            destination.chmod(0o755)
    subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
    subprocess.run(["git", "add", "."], cwd=checkout, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Release Test",
            "-c",
            "user.email=release-test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=checkout,
        check=True,
    )
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=checkout,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    manifest = _make_canonical_manifest(tmp_path, commit)
    runtime = tmp_path / "runtime.env"
    sentinels = ("SUPER_SECRET_SENTINEL", "SECRET_DB_SENTINEL")
    runtime.write_text(
        "SECRET_KEY=SUPER_SECRET_SENTINEL\n"
        "DATABASE_URL=postgresql://SECRET_DB_SENTINEL\n",
        encoding="utf-8",
    )
    _write_fake_docker(tmp_path)
    docker_log = tmp_path / "docker.log"
    output_dir = tmp_path / "prepared"
    env = os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
    }
    result = subprocess.run(
        [
            str(checkout / "scripts/prepare-production-release.sh"),
            "--canonical-manifest",
            str(manifest),
            "--runtime-env",
            str(runtime),
            "--output-directory",
            str(output_dir),
        ],
        cwd=checkout,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr

    for path in output_dir.iterdir():
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8")
        for sentinel in sentinels:
            assert sentinel not in content, path

    runtime_path = str(runtime.resolve())
    runtime_calls = [
        line for line in docker_log.read_text(encoding="utf-8").splitlines() if runtime_path in line
    ]
    assert runtime_calls
    assert all("config --quiet --no-env-resolution" in line for line in runtime_calls)


def test_local_materialization_rejects_tampered_seaweedfs_digest(tmp_path: Path) -> None:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    manifest = _make_canonical_manifest(tmp_path, commit)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["images"]["SEAWEEDFS_IMAGE"] = (
        "docker.io/chrislusf/seaweedfs@sha256:" + "2" * 64
    )
    manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "materialize-production-deployment.py"),
            "--manifest",
            str(manifest),
            "--commit",
            commit,
            "--env-output",
            str(tmp_path / "deployment.env"),
            "--metadata-output",
            str(tmp_path / "selection.json"),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "checksum" in result.stderr or "SeaweedFS" in result.stderr
