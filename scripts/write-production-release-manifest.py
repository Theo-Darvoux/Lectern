#!/usr/bin/env python3
"""Write a provenance-bound production release manifest."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from release_manifest_lib import (
    REQUIRED_WORKLOAD_PLATFORMS,
    atomic_write_json,
    parse_env_file,
    sha256_file,
    validate_inspections,
    validate_release_values,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--inspection-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    compose_paths = [repo_root / "compose.yaml", repo_root / "compose.prod.yaml"]
    try:
        values = parse_env_file(args.env_file)
        commit, profiles, images = validate_release_values(values, expected_commit=args.commit)
        inspection_payload = json.loads(args.inspection_file.read_text(encoding="utf-8"))
        if not isinstance(inspection_payload, dict):
            raise ValueError("registry inspection file must contain a JSON object")
        inspections = validate_inspections(inspection_payload, commit=commit, images=images)
        compose_hashes = {
            str(path.relative_to(repo_root)): sha256_file(path) for path in compose_paths
        }
        source_env_sha256 = sha256_file(args.env_file)
        inspection_sha256 = sha256_file(args.inspection_file)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    atomic_write_json(
        args.output,
        {
            "schema_version": 2,
            "release_commit": commit,
            "created_at": datetime.now(UTC).isoformat(),
            "compose_files": compose_hashes,
            "compose_profiles": profiles,
            "required_workload_platforms": list(REQUIRED_WORKLOAD_PLATFORMS),
            "source_env_sha256": source_env_sha256,
            "registry_inspection_sha256": inspection_sha256,
            "images": images,
            "registry_inspections": inspections["images"],
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
