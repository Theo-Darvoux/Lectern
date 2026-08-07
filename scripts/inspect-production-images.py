#!/usr/bin/env python3
"""Verify release image existence, commit-tag binding, and platform coverage."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from release_manifest_lib import (
    REQUIRED_WORKLOAD_PLATFORMS,
    WORKLOAD_IMAGES,
    atomic_write_json,
    digest_from_reference,
    parse_env_file,
    reference_for_image,
    validate_release_values,
)


def _inspect(reference: str) -> dict[str, Any]:
    result = subprocess.run(
        [
            "docker",
            "buildx",
            "imagetools",
            "inspect",
            reference,
            "--format",
            "{{json .Manifest}}",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown registry error"
        raise ValueError(f"could not inspect {reference}: {detail}")
    try:
        manifest = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"registry returned invalid manifest metadata for {reference}") from exc
    if not isinstance(manifest, dict):
        raise ValueError(f"registry returned an invalid manifest object for {reference}")
    return manifest


def _platforms(manifest: dict[str, Any]) -> list[str]:
    result: set[str] = set()
    descriptors = manifest.get("manifests", [])
    if not isinstance(descriptors, list):
        return []
    for descriptor in descriptors:
        if not isinstance(descriptor, dict):
            continue
        platform = descriptor.get("platform")
        if not isinstance(platform, dict):
            continue
        os_name = platform.get("os")
        architecture = platform.get("architecture")
        if not isinstance(os_name, str) or not isinstance(architecture, str):
            continue
        if os_name == "unknown" or architecture == "unknown":
            continue
        variant = platform.get("variant")
        normalized = f"{os_name}/{architecture}"
        if isinstance(variant, str) and variant and not (
            architecture == "arm64" and variant == "v8"
        ):
            normalized += f"/{variant}"
        result.add(normalized)
    return sorted(result)


def _verified_manifest(reference: str) -> tuple[str, list[str]]:
    expected_digest = digest_from_reference(reference)
    manifest = _inspect(reference)
    digest = manifest.get("digest")
    if digest != expected_digest:
        raise ValueError(
            f"registry digest mismatch for {reference}: expected {expected_digest}, got {digest}"
        )
    return expected_digest, _platforms(manifest)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()

    try:
        values = parse_env_file(args.env_file)
        commit, _, images = validate_release_values(values, expected_commit=args.commit)
        records: dict[str, dict[str, Any]] = {}
        for name, value in sorted(images.items()):
            reference = reference_for_image(name, value)
            digest, platforms = _verified_manifest(reference)
            commit_tag_reference: str | None = None
            commit_tag_digest: str | None = None
            if name in WORKLOAD_IMAGES:
                commit_tag_reference = f"{WORKLOAD_IMAGES[name]}:sha-{commit}"
                tagged = _inspect(commit_tag_reference)
                commit_tag_digest = tagged.get("digest")
                if commit_tag_digest != digest:
                    raise ValueError(
                        f"{commit_tag_reference} resolves to {commit_tag_digest}, expected {digest}"
                    )
                tag_platforms = _platforms(tagged)
                if tag_platforms != sorted(REQUIRED_WORKLOAD_PLATFORMS):
                    raise ValueError(
                        f"{commit_tag_reference} has platforms {tag_platforms}, expected "
                        f"{sorted(REQUIRED_WORKLOAD_PLATFORMS)}"
                    )
                if platforms != tag_platforms:
                    raise ValueError(f"digest and commit-tag platform metadata differ for {name}")
            records[name] = {
                "reference": reference,
                "digest": digest,
                "platforms": platforms,
                "commit_tag_reference": commit_tag_reference,
                "commit_tag_digest": commit_tag_digest,
            }
        atomic_write_json(
            args.output,
            {
                "schema_version": 1,
                "release_commit": commit,
                "required_workload_platforms": list(REQUIRED_WORKLOAD_PLATFORMS),
                "images": records,
            },
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
