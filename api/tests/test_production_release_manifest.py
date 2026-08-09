from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WRITE = _REPO_ROOT / "scripts/write-production-release-manifest.py"
_MATERIALIZE = _REPO_ROOT / "scripts/materialize-production-deployment.py"
_SANITIZE = _REPO_ROOT / "scripts/sanitize-production-images.py"
_INSPECT = _REPO_ROOT / "scripts/inspect-production-images.py"
_DIGEST = "1" * 64
_REDIS_DIGEST = "e7723ff73d963f5cc6d9c4643ea3d989527a402a319239054e9472a7fb9219a2"
_SEAWEED_DIGEST = "d47c7ee99fcb951351d7194915f4e3a5ea604a8e8871183d713907dec4fb9bf5"
_COMMIT = "a" * 40
_PLATFORMS = ["linux/amd64", "linux/arm64"]


def _reference(repository: str, digest: str = _DIGEST) -> str:
    return f"{repository}@sha256:{digest}"


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
        "REDIS_IMAGE": _reference("docker.io/library/redis", _REDIS_DIGEST),
        "NGINX_IMAGE": _reference("docker.io/library/nginx"),
        "MEILI_IMAGE": _reference("docker.io/getmeili/meilisearch"),
        "EUROOFFICE_IMAGE": _reference("ghcr.io/euro-office/documentserver"),
        "SEAWEEDFS_IMAGE": _reference("docker.io/chrislusf/seaweedfs", _SEAWEED_DIGEST),
    }


def _write_env(path: Path, values: dict[str, str]) -> None:
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")


def _inspection(values: dict[str, str]) -> dict[str, object]:
    images: dict[str, object] = {}
    workload_repositories = {
        "API_IMAGE": "ghcr.io/theo-darvoux/lectern/api-release",
        "WORKER_IMAGE": "ghcr.io/theo-darvoux/lectern/worker-release",
        "WEB_IMAGE": "ghcr.io/theo-darvoux/lectern/web-release",
        "SELFHOST_WORKER_IMAGE": "ghcr.io/theo-darvoux/lectern/selfhost-worker-release",
    }
    for key, value in values.items():
        if (
            key not in workload_repositories
            and not key.endswith("_IMAGE")
            and key != "POLICY_IMAGE_DIGEST"
        ):
            continue
        reference = f"docker.io/library/alpine@{value}" if key == "POLICY_IMAGE_DIGEST" else value
        digest = reference.rsplit("@", 1)[1] if "@" in reference else f"sha256:{_DIGEST}"
        repository = workload_repositories.get(key)
        images[key] = {
            "reference": reference,
            "digest": digest,
            "platforms": _PLATFORMS if repository else ["linux/amd64"],
            "commit_tag_reference": f"{repository}:sha-{_COMMIT}" if repository else None,
            "commit_tag_digest": digest if repository else None,
        }
    return {
        "schema_version": 1,
        "release_commit": _COMMIT,
        "required_workload_platforms": _PLATFORMS,
        "images": images,
    }


def _service_images(values: dict[str, str]) -> dict[str, str]:
    policy = f"docker.io/library/alpine@{values['POLICY_IMAGE_DIGEST']}"
    return dict(
        sorted(
            {
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
                "seaweedfs-master": values["SEAWEEDFS_IMAGE"],
                "seaweedfs-volume1": values["SEAWEEDFS_IMAGE"],
                "seaweedfs-volume2": values["SEAWEEDFS_IMAGE"],
                "seaweedfs-filer": values["SEAWEEDFS_IMAGE"],
                "seaweedfs-s3": values["SEAWEEDFS_IMAGE"],
            }.items()
        )
    )


def _service_map(env_file: Path, values: dict[str, str]) -> dict[str, object]:
    digest = hashlib.sha256(env_file.read_bytes()).hexdigest()
    return {
        "schema_version": 1,
        "release_commit": _COMMIT,
        "compose_profiles": ["postgres", "seaweedfs-prod", "selfhost-worker"],
        "release_input_sha256": digest,
        "services": _service_images(values),
    }


def _run_writer(tmp_path: Path, values: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env_file = tmp_path / "images.env"
    inspection_file = tmp_path / "inspection.json"
    service_map_file = tmp_path / "services.json"
    output = tmp_path / "manifest.json"
    _write_env(env_file, values)
    inspection_file.write_text(json.dumps(_inspection(values)), encoding="utf-8")
    service_map_file.write_text(json.dumps(_service_map(env_file, values)), encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            str(_WRITE),
            "--env-file",
            str(env_file),
            "--inspection-file",
            str(inspection_file),
            "--compose-service-map-file",
            str(service_map_file),
            "--output",
            str(output),
            "--commit",
            _COMMIT,
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_manifest_records_verified_images_service_mapping_and_toolchain(tmp_path: Path) -> None:
    values = _base_values()
    result = _run_writer(tmp_path, values)
    assert result.returncode == 0, result.stderr
    payload = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == 3
    assert payload["release_commit"] == _COMMIT
    assert payload["required_workload_platforms"] == _PLATFORMS
    assert set(payload["compose_files"]) == {"compose.yaml", "compose.prod.yaml"}
    assert all(len(value) == 64 for value in payload["compose_files"].values())
    assert payload["compose_service_images"]["api"] == values["API_IMAGE"]
    assert payload["compose_service_images"]["worker"] == values["WORKER_IMAGE"]
    assert payload["release_toolchain"]["BUILDX_VERSION"] == "v0.36.1"
    assert payload["release_toolchain"]["REDIS_TEST_IMAGE"] == values["REDIS_IMAGE"]
    assert payload["release_toolchain"]["SEAWEEDFS_TEST_IMAGE"] == values["SEAWEEDFS_IMAGE"]
    assert "created_at" not in payload
    assert payload["registry_inspections"]["API_IMAGE"]["commit_tag_digest"] == f"sha256:{_DIGEST}"


def test_manifest_is_byte_reproducible_for_identical_inputs(tmp_path: Path) -> None:
    values = _base_values()
    first = _run_writer(tmp_path, values)
    assert first.returncode == 0, first.stderr
    first_bytes = (tmp_path / "manifest.json").read_bytes()
    second = _run_writer(tmp_path, values)
    assert second.returncode == 0, second.stderr
    assert (tmp_path / "manifest.json").read_bytes() == first_bytes


def test_manifest_rejects_service_to_image_swap(tmp_path: Path) -> None:
    values = _base_values()
    env_file = tmp_path / "images.env"
    inspection_file = tmp_path / "inspection.json"
    service_map_file = tmp_path / "services.json"
    output = tmp_path / "manifest.json"
    _write_env(env_file, values)
    inspection_file.write_text(json.dumps(_inspection(values)), encoding="utf-8")
    service_map = _service_map(env_file, values)
    services = service_map["services"]
    assert isinstance(services, dict)
    services["api"], services["worker"] = services["worker"], services["api"]
    service_map_file.write_text(json.dumps(service_map), encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(_WRITE),
            "--env-file",
            str(env_file),
            "--inspection-file",
            str(inspection_file),
            "--compose-service-map-file",
            str(service_map_file),
            "--output",
            str(output),
            "--commit",
            _COMMIT,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "service-map" in result.stderr


def test_manifest_rejects_valid_but_untested_redis_digest(tmp_path: Path) -> None:
    values = _base_values()
    values["REDIS_IMAGE"] = _reference("docker.io/library/redis", "2" * 64)
    result = _run_writer(tmp_path, values)
    assert result.returncode != 0
    assert "Redis image differs from the repository-pinned tested digest" in result.stderr
    assert not (tmp_path / "manifest.json").exists()


_CANONICAL_KEY_ORDER = (
    "RELEASE_COMMIT",
    "COMPOSE_PROFILES",
    "API_IMAGE",
    "WORKER_IMAGE",
    "WEB_IMAGE",
    "SELFHOST_WORKER_IMAGE",
    "POLICY_IMAGE_DIGEST",
    "POSTGRES_IMAGE",
    "REDIS_IMAGE",
    "NGINX_IMAGE",
    "MEILI_IMAGE",
    "EUROOFFICE_IMAGE",
    "SEAWEEDFS_IMAGE",
)


def _canonical_json_text(payload: dict[str, object]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _rebind_manifest_to_untested_redis(payload: dict[str, object], redis_image: str) -> None:
    images = payload["images"]
    services = payload["compose_service_images"]
    inspections = payload["registry_inspections"]
    profiles = payload["compose_profiles"]
    release_commit = payload["release_commit"]
    assert isinstance(images, dict) and isinstance(services, dict) and isinstance(inspections, dict)
    assert isinstance(profiles, list) and isinstance(release_commit, str)
    images["REDIS_IMAGE"] = redis_image
    services["redis"] = redis_image
    redis_inspection = inspections["REDIS_IMAGE"]
    assert isinstance(redis_inspection, dict)
    redis_inspection["reference"] = redis_image
    redis_inspection["digest"] = redis_image.rsplit("@", 1)[1]
    env_values = {
        "RELEASE_COMMIT": release_commit,
        "COMPOSE_PROFILES": ",".join(str(item) for item in profiles),
        **{str(k): str(v) for k, v in images.items()},
    }
    source_env = "".join(
        f"{key}={env_values[key]}\n" for key in _CANONICAL_KEY_ORDER if key in env_values
    )
    source_hash = _sha256_text(source_env)
    payload["source_env_sha256"] = source_hash
    inspection_payload: dict[str, object] = {
        "schema_version": 1,
        "release_commit": release_commit,
        "required_workload_platforms": payload["required_workload_platforms"],
        "images": inspections,
    }
    payload["registry_inspection_sha256"] = _sha256_text(_canonical_json_text(inspection_payload))
    service_payload: dict[str, object] = {
        "schema_version": 1,
        "release_commit": release_commit,
        "compose_profiles": profiles,
        "release_input_sha256": source_hash,
        "services": services,
    }
    payload["compose_service_map_sha256"] = _sha256_text(_canonical_json_text(service_payload))


def test_materializer_rejects_self_consistent_untested_redis_manifest(tmp_path: Path) -> None:
    values = _base_values()
    result = _run_writer(tmp_path, values)
    assert result.returncode == 0, result.stderr
    manifest = tmp_path / "manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    _rebind_manifest_to_untested_redis(payload, _reference("docker.io/library/redis", "2" * 64))
    tampered = tmp_path / "tampered-canonical.json"
    tampered.write_text(_canonical_json_text(payload), encoding="utf-8")
    env_output = tmp_path / "deployment.env"
    metadata_output = tmp_path / "deployment.json"
    materialized = subprocess.run(
        [
            sys.executable,
            str(_MATERIALIZE),
            "--manifest",
            str(tampered),
            "--commit",
            _COMMIT,
            "--env-output",
            str(env_output),
            "--metadata-output",
            str(metadata_output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert materialized.returncode != 0
    assert "Redis image differs from the repository-pinned tested digest" in materialized.stderr
    assert not env_output.exists()
    assert not metadata_output.exists()


def test_manifest_rejects_mutable_infrastructure_tag(tmp_path: Path) -> None:
    values = _base_values()
    values["REDIS_IMAGE"] = "redis:7-alpine"
    result = _run_writer(tmp_path, values)
    assert result.returncode != 0
    assert "REDIS_IMAGE" in result.stderr


def test_manifest_rejects_registry_inspection_not_bound_to_commit_tag(tmp_path: Path) -> None:
    values = _base_values()
    env_file = tmp_path / "images.env"
    inspection_file = tmp_path / "inspection.json"
    service_map_file = tmp_path / "services.json"
    output = tmp_path / "manifest.json"
    _write_env(env_file, values)
    inspection = _inspection(values)
    inspection["images"]["API_IMAGE"]["commit_tag_digest"] = "sha256:" + "2" * 64
    inspection_file.write_text(json.dumps(inspection), encoding="utf-8")
    service_map_file.write_text(json.dumps(_service_map(env_file, values)), encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(_WRITE),
            "--env-file",
            str(env_file),
            "--inspection-file",
            str(inspection_file),
            "--compose-service-map-file",
            str(service_map_file),
            "--output",
            str(output),
            "--commit",
            _COMMIT,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "commit tag digest mismatch" in result.stderr


def test_sanitizer_rejects_runtime_and_arbitrary_variables(tmp_path: Path) -> None:
    for variable in ("ENVIRONMENT", "DATABASE_URL", "SECRET_KEY", "MEILI_MASTER_KEY", "ARBITRARY"):
        values = _base_values()
        env_file = tmp_path / f"{variable}.env"
        output = tmp_path / f"{variable}.sanitized"
        _write_env(env_file, values)
        with env_file.open("a", encoding="utf-8") as stream:
            stream.write(f"{variable}=unexpected\n")
        result = subprocess.run(
            [
                sys.executable,
                str(_SANITIZE),
                "--env-file",
                str(env_file),
                "--output",
                str(output),
                "--commit",
                _COMMIT,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert variable in result.stderr
        assert not output.exists()


def test_sanitizer_rejects_duplicate_export_quoted_and_spaced_assignments(tmp_path: Path) -> None:
    base = _base_values()
    cases = {
        "duplicate": "API_IMAGE=" + base["API_IMAGE"] + "\n",
        "export": "export API_IMAGE=" + base["API_IMAGE"] + "\n",
        "quoted": 'API_IMAGE="' + base["API_IMAGE"] + '"\n',
        "spaced": "API_IMAGE =" + base["API_IMAGE"] + "\n",
    }
    for name, bad_line in cases.items():
        env_file = tmp_path / f"{name}.env"
        if name == "duplicate":
            _write_env(env_file, base)
            env_file.write_text(env_file.read_text(encoding="utf-8") + bad_line, encoding="utf-8")
        else:
            lines = [f"{key}={value}\n" for key, value in base.items() if key != "API_IMAGE"]
            env_file.write_text("".join(lines) + bad_line, encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                str(_SANITIZE),
                "--env-file",
                str(env_file),
                "--output",
                str(tmp_path / f"{name}.out"),
                "--commit",
                _COMMIT,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0, name


def _fake_docker(tmp_path: Path) -> Path:
    executable = tmp_path / "docker"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "ref = sys.argv[4]\n"
        "digest = ref.rsplit('@', 1)[1] if '@' in ref else 'sha256:' + '1' * 64\n"
        "if os.environ.get('WRONG_TAG') == '1' and ':sha-' in ref:\n"
        "    digest = 'sha256:' + '2' * 64\n"
        "print(json.dumps({'digest': digest, 'manifests': [\n"
        " {'platform': {'os': 'linux', 'architecture': 'amd64'}},\n"
        " {'platform': {'os': 'linux', 'architecture': 'arm64'}},\n"
        " {'platform': {'os': 'unknown', 'architecture': 'unknown'}}\n"
        "]}))\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def test_registry_inspector_rejects_commit_tag_digest_mismatch(tmp_path: Path) -> None:
    _fake_docker(tmp_path)
    env_file = tmp_path / "images.env"
    output = tmp_path / "inspection.json"
    _write_env(env_file, _base_values())
    env = os.environ | {"PATH": f"{tmp_path}:{os.environ['PATH']}", "WRONG_TAG": "1"}
    result = subprocess.run(
        [
            sys.executable,
            str(_INSPECT),
            "--env-file",
            str(env_file),
            "--output",
            str(output),
            "--commit",
            _COMMIT,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode != 0
    assert "resolves to" in result.stderr
    assert not output.exists()
