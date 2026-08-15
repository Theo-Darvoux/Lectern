"""Shared validation helpers for production release manifests."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

DIGEST_PATTERN = r"sha256:[0-9a-f]{64}"
HEX_SHA256_RE = re.compile(r"[0-9a-f]{64}")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
REQUIRED_WORKLOAD_PLATFORMS = ("linux/amd64", "linux/arm64")
RELEASE_MANIFEST_SCHEMA_VERSION = 3
COMPOSE_SERVICE_MAP_SCHEMA_VERSION = 1

IMAGE_PATTERNS: dict[str, str] = {
    "API_IMAGE": rf"ghcr\.io/theo-darvoux/lectern/api-release@{DIGEST_PATTERN}",
    "WORKER_IMAGE": rf"ghcr\.io/theo-darvoux/lectern/worker-release@{DIGEST_PATTERN}",
    "WEB_IMAGE": rf"ghcr\.io/theo-darvoux/lectern/web-release@{DIGEST_PATTERN}",
    "SELFHOST_WORKER_IMAGE": rf"ghcr\.io/theo-darvoux/lectern/selfhost-worker-release@{DIGEST_PATTERN}",
    "POLICY_IMAGE_DIGEST": DIGEST_PATTERN,
    "POSTGRES_IMAGE": rf"docker\.io/library/postgres@{DIGEST_PATTERN}",
    "REDIS_IMAGE": rf"docker\.io/library/redis@{DIGEST_PATTERN}",
    "NGINX_IMAGE": rf"docker\.io/library/nginx@{DIGEST_PATTERN}",
    "MEILI_IMAGE": rf"docker\.io/getmeili/meilisearch@{DIGEST_PATTERN}",
    "EUROOFFICE_IMAGE": rf"ghcr\.io/euro-office/documentserver@{DIGEST_PATTERN}",
    "SEAWEEDFS_IMAGE": rf"docker\.io/chrislusf/seaweedfs@{DIGEST_PATTERN}",
}

ALLOWED_KEYS = frozenset({"RELEASE_COMMIT", "COMPOSE_PROFILES", *IMAGE_PATTERNS})
ALWAYS_REQUIRED = frozenset(
    {
        "API_IMAGE",
        "WORKER_IMAGE",
        "WEB_IMAGE",
        "POLICY_IMAGE_DIGEST",
        "REDIS_IMAGE",
        "NGINX_IMAGE",
        "MEILI_IMAGE",
        "EUROOFFICE_IMAGE",
    }
)
PROFILE_IMAGES = {
    "postgres": "POSTGRES_IMAGE",
    "seaweedfs-prod": "SEAWEEDFS_IMAGE",
    "selfhost-worker": "SELFHOST_WORKER_IMAGE",
}
ALLOWED_PROFILES = frozenset(PROFILE_IMAGES)
WORKLOAD_IMAGES = {
    "API_IMAGE": "ghcr.io/theo-darvoux/lectern/api-release",
    "WORKER_IMAGE": "ghcr.io/theo-darvoux/lectern/worker-release",
    "WEB_IMAGE": "ghcr.io/theo-darvoux/lectern/web-release",
    "SELFHOST_WORKER_IMAGE": "ghcr.io/theo-darvoux/lectern/selfhost-worker-release",
}
CANONICAL_KEY_ORDER = (
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

RELEASE_TOOLCHAIN_KEYS = (
    "BUILDX_VERSION",
    "BUILDKIT_VERSION",
    "BUILDKIT_IMAGE",
    "BINFMT_VERSION",
    "BINFMT_IMAGE",
    "REDIS_VERSION",
    "REDIS_TEST_IMAGE",
    "SEAWEEDFS_VERSION",
    "SEAWEEDFS_TEST_IMAGE",
)


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        Path(temporary).replace(path)
    finally:
        try:
            Path(temporary).unlink()
        except FileNotFoundError:
            pass


def canonical_json_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(path, canonical_json_text(payload))


def sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a deliberately narrow, non-shell KEY=VALUE release file."""
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not raw_line:
            continue
        if raw_line.startswith("#"):
            continue
        if raw_line != raw_line.strip():
            raise ValueError(
                f"{path}:{line_number}: leading/trailing whitespace is forbidden"
            )
        if raw_line.startswith("export "):
            raise ValueError(
                f"{path}:{line_number}: shell-style export assignments are forbidden"
            )
        if "=" not in raw_line:
            raise ValueError(f"{path}:{line_number}: expected KEY=VALUE")
        key, value = raw_line.split("=", 1)
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", key) is None:
            raise ValueError(f"{path}:{line_number}: invalid variable name {key!r}")
        if key not in ALLOWED_KEYS:
            raise ValueError(
                f"{path}:{line_number}: unsupported release variable {key}"
            )
        if key in values:
            raise ValueError(f"{path}:{line_number}: duplicate variable {key}")
        if key != "COMPOSE_PROFILES" and not value:
            raise ValueError(f"{path}:{line_number}: {key} must not be empty")
        if value and (value[0] in {'"', "'"} or value[-1] in {'"', "'"}):
            raise ValueError(
                f"{path}:{line_number}: quoted or shell-like values are forbidden"
            )
        if any(character.isspace() for character in value):
            raise ValueError(f"{path}:{line_number}: whitespace in values is forbidden")
        values[key] = value
    return values


def parse_release_toolchain(path: Path) -> dict[str, str]:
    """Parse the repository-pinned release control-plane inputs."""
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not raw_line or raw_line.startswith("#"):
            continue
        if raw_line != raw_line.strip() or "=" not in raw_line:
            raise ValueError(f"{path}:{line_number}: expected exact KEY=VALUE")
        key, value = raw_line.split("=", 1)
        if key not in RELEASE_TOOLCHAIN_KEYS:
            raise ValueError(
                f"{path}:{line_number}: unsupported release toolchain key {key}"
            )
        if key in values:
            raise ValueError(
                f"{path}:{line_number}: duplicate release toolchain key {key}"
            )
        if not value or any(character.isspace() for character in value):
            raise ValueError(
                f"{path}:{line_number}: invalid release toolchain value for {key}"
            )
        values[key] = value

    if set(values) != set(RELEASE_TOOLCHAIN_KEYS):
        missing = sorted(set(RELEASE_TOOLCHAIN_KEYS) - set(values))
        extra = sorted(set(values) - set(RELEASE_TOOLCHAIN_KEYS))
        detail: list[str] = []
        if missing:
            detail.append("missing: " + ", ".join(missing))
        if extra:
            detail.append("unexpected: " + ", ".join(extra))
        raise ValueError(
            "release toolchain keys do not match policy (" + "; ".join(detail) + ")"
        )

    if re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", values["BUILDX_VERSION"]) is None:
        raise ValueError("BUILDX_VERSION must be an exact semantic version")
    if re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", values["BUILDKIT_VERSION"]) is None:
        raise ValueError("BUILDKIT_VERSION must be an exact semantic version")
    if (
        re.fullmatch(r"qemu-v[0-9]+\.[0-9]+\.[0-9]+-[0-9]+", values["BINFMT_VERSION"])
        is None
    ):
        raise ValueError("BINFMT_VERSION must identify an exact qemu release build")
    if re.fullmatch(r"[0-9]+\.[0-9]+", values["REDIS_VERSION"]) is None:
        raise ValueError("REDIS_VERSION must identify an exact reviewed release")
    if re.fullmatch(r"[0-9]+\.[0-9]+", values["SEAWEEDFS_VERSION"]) is None:
        raise ValueError("SEAWEEDFS_VERSION must identify an exact reviewed release")

    immutable_patterns = {
        "BUILDKIT_IMAGE": rf"docker\.io/moby/buildkit@{DIGEST_PATTERN}",
        "BINFMT_IMAGE": rf"docker\.io/tonistiigi/binfmt@{DIGEST_PATTERN}",
        "REDIS_TEST_IMAGE": rf"docker\.io/library/redis@{DIGEST_PATTERN}",
        "SEAWEEDFS_TEST_IMAGE": rf"docker\.io/chrislusf/seaweedfs@{DIGEST_PATTERN}",
    }
    for key, pattern in immutable_patterns.items():
        if re.fullmatch(pattern, values[key]) is None:
            raise ValueError(f"{key} must be an immutable canonical digest reference")
    return {key: values[key] for key in RELEASE_TOOLCHAIN_KEYS}


def validate_tested_infrastructure_images(
    images: dict[str, str], toolchain: dict[str, str]
) -> None:
    """Require deployment-critical infrastructure to equal required-CI inputs."""
    for image_key, toolchain_key, label in (
        ("REDIS_IMAGE", "REDIS_TEST_IMAGE", "Redis"),
        ("SEAWEEDFS_IMAGE", "SEAWEEDFS_TEST_IMAGE", "SeaweedFS"),
    ):
        image = images.get(image_key)
        if image is not None and image != toolchain[toolchain_key]:
            raise ValueError(
                f"release {label} image differs from the repository-pinned tested digest"
            )


def parse_profiles(raw: str) -> list[str]:
    if raw == "":
        return []
    parts = raw.split(",")
    if any(not item for item in parts):
        raise ValueError("COMPOSE_PROFILES contains an empty or malformed profile")
    if len(parts) != len(set(parts)):
        raise ValueError("COMPOSE_PROFILES contains duplicate profiles")
    profiles = sorted(parts)
    unknown = sorted(set(profiles) - ALLOWED_PROFILES)
    if unknown:
        raise ValueError(
            "unsupported production Compose profiles: " + ", ".join(unknown)
        )
    return profiles


def validate_release_values(
    values: dict[str, str], *, expected_commit: str | None = None
) -> tuple[str, list[str], dict[str, str]]:
    file_commit = values.get("RELEASE_COMMIT", "")
    commit = expected_commit or file_commit
    if COMMIT_RE.fullmatch(commit) is None:
        raise ValueError("RELEASE_COMMIT must be a lowercase 40-hex Git commit")
    if not file_commit:
        raise ValueError("RELEASE_COMMIT is required")
    if expected_commit and file_commit != expected_commit:
        raise ValueError("RELEASE_COMMIT does not match the checked-out Git commit")
    if "COMPOSE_PROFILES" not in values:
        raise ValueError("COMPOSE_PROFILES must be present")

    profiles = parse_profiles(values["COMPOSE_PROFILES"])
    required = set(ALWAYS_REQUIRED)
    required.update(PROFILE_IMAGES[profile] for profile in profiles)

    disabled_profile_keys = {
        image_key
        for profile, image_key in PROFILE_IMAGES.items()
        if profile not in profiles
    }
    unexpected_profile_keys = sorted(disabled_profile_keys & values.keys())
    if unexpected_profile_keys:
        raise ValueError(
            "profile-only image variables are forbidden when their profile is disabled: "
            + ", ".join(unexpected_profile_keys)
        )

    errors: list[str] = []
    images: dict[str, str] = {}
    for name in sorted(required):
        value = values.get(name, "")
        pattern = IMAGE_PATTERNS[name]
        if re.fullmatch(pattern, value) is None:
            errors.append(f"{name} must match {pattern}")
        else:
            images[name] = value
    if errors:
        raise ValueError("invalid production image policy:\n- " + "\n- ".join(errors))
    return commit, profiles, images


def canonical_env_text(commit: str, profiles: list[str], images: dict[str, str]) -> str:
    values = {
        "RELEASE_COMMIT": commit,
        "COMPOSE_PROFILES": ",".join(profiles),
        **images,
    }
    return "".join(
        f"{key}={values[key]}\n" for key in CANONICAL_KEY_ORDER if key in values
    )


def reference_for_image(name: str, value: str) -> str:
    if name == "POLICY_IMAGE_DIGEST":
        return f"docker.io/library/alpine@{value}"
    return value


def expected_compose_images(images: dict[str, str]) -> set[str]:
    return {reference_for_image(name, value) for name, value in images.items()}


def expected_compose_service_images(images: dict[str, str]) -> dict[str, str]:
    """Return the exact production service→image mapping for an enabled profile set."""
    policy = reference_for_image("POLICY_IMAGE_DIGEST", images["POLICY_IMAGE_DIGEST"])
    services = {
        "release-image-policy": policy,
        "redis": images["REDIS_IMAGE"],
        "meilisearch": images["MEILI_IMAGE"],
        "eurooffice": images["EUROOFFICE_IMAGE"],
        "api": images["API_IMAGE"],
        "worker": images["WORKER_IMAGE"],
        "worker-fast": images["WORKER_IMAGE"],
        "worker-slow": images["WORKER_IMAGE"],
        "web": images["WEB_IMAGE"],
        "nginx": images["NGINX_IMAGE"],
    }
    if "POSTGRES_IMAGE" in images:
        services["postgres-image-policy"] = policy
        services["postgres"] = images["POSTGRES_IMAGE"]
    if "SELFHOST_WORKER_IMAGE" in images:
        services["selfhost-worker-image-policy"] = policy
        services["selfhost-worker"] = images["SELFHOST_WORKER_IMAGE"]
    if "SEAWEEDFS_IMAGE" in images:
        services["seaweedfs-image-policy"] = policy
        for name in (
            "seaweedfs-master",
            "seaweedfs-volume1",
            "seaweedfs-volume2",
            "seaweedfs-filer",
            "seaweedfs-s3",
        ):
            services[name] = images["SEAWEEDFS_IMAGE"]
    return dict(sorted(services.items()))


def digest_from_reference(reference: str) -> str:
    try:
        digest = reference.rsplit("@", 1)[1]
    except IndexError as exc:
        raise ValueError(f"image reference is not digest-pinned: {reference}") from exc
    if re.fullmatch(DIGEST_PATTERN, digest) is None:
        raise ValueError(f"invalid image digest in reference: {reference}")
    return digest


def validate_inspections(
    payload: dict[str, Any], *, commit: str, images: dict[str, str]
) -> dict[str, Any]:
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported registry inspection schema")
    if payload.get("release_commit") != commit:
        raise ValueError("registry inspection commit does not match release commit")
    expected_platforms = list(REQUIRED_WORKLOAD_PLATFORMS)
    if payload.get("required_workload_platforms") != expected_platforms:
        raise ValueError("registry inspection platform policy does not match")
    records = payload.get("images")
    if not isinstance(records, dict) or set(records) != set(images):
        raise ValueError(
            "registry inspection image set does not match release image set"
        )

    for name, value in images.items():
        record = records.get(name)
        if not isinstance(record, dict):
            raise ValueError(f"missing registry inspection for {name}")
        reference = reference_for_image(name, value)
        expected_digest = digest_from_reference(reference)
        if record.get("reference") != reference:
            raise ValueError(f"registry inspection reference mismatch for {name}")
        if record.get("digest") != expected_digest:
            raise ValueError(f"registry inspection digest mismatch for {name}")
        platforms = record.get("platforms")
        if not isinstance(platforms, list) or not all(
            isinstance(item, str) for item in platforms
        ):
            raise ValueError(f"invalid registry platform list for {name}")
        if name in WORKLOAD_IMAGES:
            expected_tag = f"{WORKLOAD_IMAGES[name]}:sha-{commit}"
            if record.get("commit_tag_reference") != expected_tag:
                raise ValueError(f"commit tag reference mismatch for {name}")
            if record.get("commit_tag_digest") != expected_digest:
                raise ValueError(f"commit tag digest mismatch for {name}")
            if sorted(platforms) != sorted(REQUIRED_WORKLOAD_PLATFORMS):
                raise ValueError(f"workload platform set mismatch for {name}")
        elif (
            record.get("commit_tag_reference") is not None
            or record.get("commit_tag_digest") is not None
        ):
            raise ValueError(
                f"infrastructure inspection unexpectedly contains a commit tag for {name}"
            )
    return payload


def validate_compose_service_map(
    payload: dict[str, Any],
    *,
    commit: str,
    profiles: list[str],
    images: dict[str, str],
    release_input_sha256: str,
) -> dict[str, Any]:
    if payload.get("schema_version") != COMPOSE_SERVICE_MAP_SCHEMA_VERSION:
        raise ValueError("unsupported Compose service-map schema")
    if payload.get("release_commit") != commit:
        raise ValueError("Compose service-map commit does not match release commit")
    if payload.get("compose_profiles") != profiles:
        raise ValueError("Compose service-map profiles do not match release input")
    if payload.get("release_input_sha256") != release_input_sha256:
        raise ValueError("Compose service-map is not bound to the release input")
    services = payload.get("services")
    if not isinstance(services, dict) or not all(
        isinstance(name, str) and isinstance(image, str)
        for name, image in services.items()
    ):
        raise ValueError("Compose service-map services must be a string map")
    expected = expected_compose_service_images(images)
    actual = dict(sorted(services.items()))
    if actual != expected:
        raise ValueError("Compose service-map does not match the release image policy")
    return payload


def validate_canonical_release_manifest(
    payload: dict[str, Any],
    *,
    repo_root: Path,
    expected_commit: str,
    toolchain_path: Path | None = None,
) -> tuple[str, list[str], dict[str, str], dict[str, str]]:
    """Validate a canonical artifact before deriving any local deployment inputs."""
    if payload.get("schema_version") != RELEASE_MANIFEST_SCHEMA_VERSION:
        raise ValueError("unsupported production release manifest schema")
    if payload.get("release_commit") != expected_commit:
        raise ValueError(
            "canonical release manifest does not match the checked-out commit"
        )

    raw_profiles = payload.get("compose_profiles")
    if not isinstance(raw_profiles, list) or not all(
        isinstance(item, str) for item in raw_profiles
    ):
        raise ValueError("canonical manifest has an invalid Compose profile list")
    profiles = parse_profiles(",".join(raw_profiles))
    if raw_profiles != profiles:
        raise ValueError("canonical manifest Compose profiles are not canonical")

    raw_images = payload.get("images")
    if not isinstance(raw_images, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in raw_images.items()
    ):
        raise ValueError("canonical manifest has an invalid image map")
    values = {
        "RELEASE_COMMIT": expected_commit,
        "COMPOSE_PROFILES": ",".join(profiles),
        **raw_images,
    }
    _, _, images = validate_release_values(values, expected_commit=expected_commit)
    if images != raw_images:
        raise ValueError("canonical manifest image map is not canonical")

    compose_files = payload.get("compose_files")
    expected_compose_paths = (
        repo_root / "compose.yaml",
        repo_root / "compose.prod.yaml",
    )
    expected_hashes = {
        str(path.relative_to(repo_root)): sha256_file(path)
        for path in expected_compose_paths
    }
    if compose_files != expected_hashes:
        raise ValueError(
            "canonical manifest Compose hashes do not match the checked-out release"
        )

    if payload.get("required_workload_platforms") != list(REQUIRED_WORKLOAD_PLATFORMS):
        raise ValueError("canonical manifest workload platform policy does not match")

    source_env_text = canonical_env_text(expected_commit, profiles, images)
    source_env_sha256 = sha256_text(source_env_text)
    if payload.get("source_env_sha256") != source_env_sha256:
        raise ValueError("canonical manifest release-input checksum is inconsistent")

    inspection_records = payload.get("registry_inspections")
    inspection_payload = {
        "schema_version": 1,
        "release_commit": expected_commit,
        "required_workload_platforms": list(REQUIRED_WORKLOAD_PLATFORMS),
        "images": inspection_records,
    }
    if not isinstance(inspection_records, dict):
        raise ValueError("canonical manifest registry inspections are missing")
    validate_inspections(inspection_payload, commit=expected_commit, images=images)
    if payload.get("registry_inspection_sha256") != sha256_text(
        canonical_json_text(inspection_payload)
    ):
        raise ValueError(
            "canonical manifest registry-inspection checksum is inconsistent"
        )

    toolchain_path = toolchain_path or repo_root / "deploy/release-toolchain.env"
    toolchain = parse_release_toolchain(toolchain_path)
    if payload.get("release_toolchain") != toolchain:
        raise ValueError(
            "canonical manifest release toolchain does not match the checkout"
        )
    if payload.get("release_toolchain_sha256") != sha256_file(toolchain_path):
        raise ValueError("canonical manifest release toolchain checksum does not match")
    validate_tested_infrastructure_images(images, toolchain)

    service_map = payload.get("compose_service_images")
    if service_map != expected_compose_service_images(images):
        raise ValueError(
            "canonical manifest service→image mapping does not match its image policy"
        )
    service_map_payload = {
        "schema_version": COMPOSE_SERVICE_MAP_SCHEMA_VERSION,
        "release_commit": expected_commit,
        "compose_profiles": profiles,
        "release_input_sha256": source_env_sha256,
        "services": service_map,
    }
    if payload.get("compose_service_map_sha256") != sha256_text(
        canonical_json_text(service_map_payload)
    ):
        raise ValueError(
            "canonical manifest Compose service-map checksum is inconsistent"
        )

    for hash_key in (
        "source_env_sha256",
        "registry_inspection_sha256",
        "compose_service_map_sha256",
        "release_toolchain_sha256",
    ):
        value = payload.get(hash_key)
        if not isinstance(value, str) or HEX_SHA256_RE.fullmatch(value) is None:
            raise ValueError(f"canonical manifest has an invalid {hash_key}")

    return expected_commit, profiles, images, toolchain
