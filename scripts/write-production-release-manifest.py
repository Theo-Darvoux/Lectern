#!/usr/bin/env python3
"""Write a deterministic provenance-bound production release manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from release_manifest_lib import (
    RELEASE_MANIFEST_SCHEMA_VERSION,
    REQUIRED_WORKLOAD_PLATFORMS,
    atomic_write_json,
    parse_env_file,
    parse_release_toolchain,
    sha256_file,
    validate_compose_service_map,
    validate_inspections,
    validate_release_values,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--inspection-file", type=Path, required=True)
    parser.add_argument("--compose-service-map-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument(
        "--toolchain-file",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "deploy/release-toolchain.env",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    compose_paths = [repo_root / "compose.yaml", repo_root / "compose.prod.yaml"]
    try:
        values = parse_env_file(args.env_file)
        commit, profiles, images = validate_release_values(
            values, expected_commit=args.commit
        )
        inspection_payload = json.loads(
            args.inspection_file.read_text(encoding="utf-8")
        )
        if not isinstance(inspection_payload, dict):
            raise ValueError("registry inspection file must contain a JSON object")
        inspections = validate_inspections(
            inspection_payload, commit=commit, images=images
        )

        service_map_payload = json.loads(
            args.compose_service_map_file.read_text(encoding="utf-8")
        )
        if not isinstance(service_map_payload, dict):
            raise ValueError("Compose service-map file must contain a JSON object")
        source_env_sha256 = sha256_file(args.env_file)
        service_map = validate_compose_service_map(
            service_map_payload,
            commit=commit,
            profiles=profiles,
            images=images,
            release_input_sha256=source_env_sha256,
        )

        toolchain = parse_release_toolchain(args.toolchain_file)
        if (
            images.get("SEAWEEDFS_IMAGE")
            and images["SEAWEEDFS_IMAGE"] != toolchain["SEAWEEDFS_TEST_IMAGE"]
        ):
            raise ValueError(
                "release SeaweedFS image differs from the repository-pinned tested digest"
            )

        compose_hashes = {
            str(path.relative_to(repo_root)): sha256_file(path)
            for path in compose_paths
        }
        inspection_sha256 = sha256_file(args.inspection_file)
        service_map_sha256 = sha256_file(args.compose_service_map_file)
        toolchain_sha256 = sha256_file(args.toolchain_file)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    # Intentionally omit wall-clock timestamps: identical trusted inputs produce
    # byte-identical canonical manifests on safe reruns.
    atomic_write_json(
        args.output,
        {
            "schema_version": RELEASE_MANIFEST_SCHEMA_VERSION,
            "release_commit": commit,
            "compose_files": compose_hashes,
            "compose_profiles": profiles,
            "required_workload_platforms": list(REQUIRED_WORKLOAD_PLATFORMS),
            "source_env_sha256": source_env_sha256,
            "registry_inspection_sha256": inspection_sha256,
            "compose_service_map_sha256": service_map_sha256,
            "release_toolchain_sha256": toolchain_sha256,
            "release_toolchain": toolchain,
            "images": images,
            "compose_service_images": service_map["services"],
            "registry_inspections": inspections["images"],
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
