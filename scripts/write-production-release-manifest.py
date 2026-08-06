#!/usr/bin/env python3
"""Validate production image references and write a non-secret release manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

_DIGEST = r"sha256:[0-9a-f]{64}"
_IMAGE_PATTERNS = {
    "API_IMAGE": rf"ghcr\.io/theo-darvoux/lectern/api-release@{_DIGEST}",
    "WORKER_IMAGE": rf"ghcr\.io/theo-darvoux/lectern/worker-release@{_DIGEST}",
    "WEB_IMAGE": rf"ghcr\.io/theo-darvoux/lectern/web-release@{_DIGEST}",
    "SELFHOST_WORKER_IMAGE": rf"ghcr\.io/theo-darvoux/lectern/selfhost-worker-release@{_DIGEST}",
    "POLICY_IMAGE_DIGEST": _DIGEST,
    "POSTGRES_IMAGE": rf"docker\.io/library/postgres@{_DIGEST}",
    "REDIS_IMAGE": rf"docker\.io/library/redis@{_DIGEST}",
    "NGINX_IMAGE": rf"docker\.io/library/nginx@{_DIGEST}",
    "MEILI_IMAGE": rf"docker\.io/getmeili/meilisearch@{_DIGEST}",
    "EUROOFFICE_IMAGE": rf"ghcr\.io/euro-office/documentserver@{_DIGEST}",
    "SEAWEEDFS_IMAGE": rf"docker\.io/chrislusf/seaweedfs@{_DIGEST}",
}
_ALWAYS_REQUIRED = {
    "API_IMAGE",
    "WORKER_IMAGE",
    "WEB_IMAGE",
    "POLICY_IMAGE_DIGEST",
    "REDIS_IMAGE",
    "NGINX_IMAGE",
    "MEILI_IMAGE",
    "EUROOFFICE_IMAGE",
}
_PROFILE_IMAGES = {
    "postgres": "POSTGRES_IMAGE",
    "seaweedfs-prod": "SEAWEEDFS_IMAGE",
    "selfhost-worker": "SELFHOST_WORKER_IMAGE",
}
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_ALLOWED_PROFILES = frozenset(_PROFILE_IMAGES)


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"{path}:{line_number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError(f"{path}:{line_number}: invalid variable name {key!r}")
        if key in values:
            raise ValueError(f"{path}:{line_number}: duplicate variable {key}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def _profiles(raw: str) -> list[str]:
    profiles = sorted({item.strip() for item in raw.split(",") if item.strip()})
    unknown = sorted(set(profiles) - _ALLOWED_PROFILES)
    if unknown:
        raise ValueError("unsupported production Compose profiles: " + ", ".join(unknown))
    return profiles


def _validated_images(values: dict[str, str], profiles: list[str]) -> dict[str, str]:
    required = set(_ALWAYS_REQUIRED)
    required.update(_PROFILE_IMAGES[p] for p in profiles if p in _PROFILE_IMAGES)
    errors: list[str] = []
    images: dict[str, str] = {}
    for name in sorted(required):
        value = values.get(name, "")
        pattern = _IMAGE_PATTERNS[name]
        if re.fullmatch(pattern, value) is None:
            errors.append(f"{name} must match {pattern}")
        else:
            images[name] = value
    if errors:
        raise ValueError("invalid production image policy:\n- " + "\n- ".join(errors))
    return images


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--commit")
    args = parser.parse_args()

    try:
        values = _parse_env_file(args.env_file)
        file_commit = values.get("RELEASE_COMMIT", "")
        commit = args.commit or file_commit
        if _COMMIT_RE.fullmatch(commit) is None:
            raise ValueError("RELEASE_COMMIT/--commit must be a lowercase 40-hex Git commit")
        if args.commit and file_commit and file_commit != args.commit:
            raise ValueError("RELEASE_COMMIT does not match the checked-out Git commit")
        if "COMPOSE_PROFILES" not in values:
            raise ValueError("COMPOSE_PROFILES must be present, even when no profiles are enabled")
        profiles = _profiles(values["COMPOSE_PROFILES"])
        images = _validated_images(values, profiles)
        source_bytes = args.env_file.read_bytes()
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    payload: dict[str, object] = {
        "schema_version": 1,
        "release_commit": commit,
        "created_at": datetime.now(UTC).isoformat(),
        "compose_files": ["compose.yaml", "compose.prod.yaml"],
        "compose_profiles": profiles,
        "source_env_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "images": images,
    }
    _atomic_json(args.output, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
