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
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
REQUIRED_WORKLOAD_PLATFORMS = ("linux/amd64", "linux/arm64")

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


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a deliberately narrow, non-shell KEY=VALUE release file."""
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line:
            continue
        if raw_line.startswith("#"):
            continue
        if raw_line != raw_line.strip():
            raise ValueError(f"{path}:{line_number}: leading/trailing whitespace is forbidden")
        if raw_line.startswith("export "):
            raise ValueError(f"{path}:{line_number}: shell-style export assignments are forbidden")
        if "=" not in raw_line:
            raise ValueError(f"{path}:{line_number}: expected KEY=VALUE")
        key, value = raw_line.split("=", 1)
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", key) is None:
            raise ValueError(f"{path}:{line_number}: invalid variable name {key!r}")
        if key not in ALLOWED_KEYS:
            raise ValueError(f"{path}:{line_number}: unsupported release variable {key}")
        if key in values:
            raise ValueError(f"{path}:{line_number}: duplicate variable {key}")
        if not value:
            raise ValueError(f"{path}:{line_number}: {key} must not be empty")
        if value[0] in {'"', "'"} or value[-1] in {'"', "'"}:
            raise ValueError(f"{path}:{line_number}: quoted or shell-like values are forbidden")
        if any(character.isspace() for character in value):
            raise ValueError(f"{path}:{line_number}: whitespace in values is forbidden")
        values[key] = value
    return values


def parse_profiles(raw: str) -> list[str]:
    parts = raw.split(",")
    if not raw or any(not item for item in parts):
        raise ValueError("COMPOSE_PROFILES contains an empty or malformed profile")
    if len(parts) != len(set(parts)):
        raise ValueError("COMPOSE_PROFILES contains duplicate profiles")
    profiles = sorted(parts)
    unknown = sorted(set(profiles) - ALLOWED_PROFILES)
    if unknown:
        raise ValueError("unsupported production Compose profiles: " + ", ".join(unknown))
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
        image_key for profile, image_key in PROFILE_IMAGES.items() if profile not in profiles
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
    return "".join(f"{key}={values[key]}\n" for key in CANONICAL_KEY_ORDER if key in values)


def reference_for_image(name: str, value: str) -> str:
    if name == "POLICY_IMAGE_DIGEST":
        return f"docker.io/library/alpine@{value}"
    return value


def expected_compose_images(images: dict[str, str]) -> set[str]:
    return {reference_for_image(name, value) for name, value in images.items()}


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
        raise ValueError("registry inspection image set does not match release image set")

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
        if not isinstance(platforms, list) or not all(isinstance(item, str) for item in platforms):
            raise ValueError(f"invalid registry platform list for {name}")
        if name in WORKLOAD_IMAGES:
            expected_tag = f"{WORKLOAD_IMAGES[name]}:sha-{commit}"
            if record.get("commit_tag_reference") != expected_tag:
                raise ValueError(f"commit tag reference mismatch for {name}")
            if record.get("commit_tag_digest") != expected_digest:
                raise ValueError(f"commit tag digest mismatch for {name}")
            if sorted(platforms) != sorted(REQUIRED_WORKLOAD_PLATFORMS):
                raise ValueError(f"workload platform set mismatch for {name}")
        elif record.get("commit_tag_reference") is not None or record.get("commit_tag_digest") is not None:
            raise ValueError(f"infrastructure inspection unexpectedly contains a commit tag for {name}")
    return payload
