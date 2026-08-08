#!/usr/bin/env python3
"""Derive deployment image inputs only from a canonical release manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from release_manifest_lib import (
    ALWAYS_REQUIRED,
    PROFILE_IMAGES,
    atomic_write_json,
    atomic_write_text,
    canonical_env_text,
    parse_profiles,
    sha256_file,
    validate_canonical_release_manifest,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--profiles", default=None)
    parser.add_argument("--env-output", type=Path, required=True)
    parser.add_argument("--metadata-output", type=Path, required=True)
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    try:
        payload = json.loads(args.manifest.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("canonical manifest must contain a JSON object")
        commit, canonical_profiles, canonical_images, toolchain = validate_canonical_release_manifest(
            payload,
            repo_root=repo_root,
            expected_commit=args.commit,
        )
        selected_profiles = (
            canonical_profiles if args.profiles is None else parse_profiles(args.profiles)
        )
        unavailable = sorted(set(selected_profiles) - set(canonical_profiles))
        if unavailable:
            raise ValueError(
                "requested deployment profiles were not certified by the canonical release: "
                + ", ".join(unavailable)
            )

        selected_keys = set(ALWAYS_REQUIRED)
        selected_keys.update(PROFILE_IMAGES[profile] for profile in selected_profiles)
        selected_images = {
            key: canonical_images[key] for key in sorted(selected_keys)
        }
        env_text = canonical_env_text(commit, selected_profiles, selected_images)
        manifest_sha256 = sha256_file(args.manifest)
    except (OSError, ValueError, json.JSONDecodeError, KeyError) as exc:
        parser.error(str(exc))

    atomic_write_text(args.env_output, env_text)
    atomic_write_json(
        args.metadata_output,
        {
            "schema_version": 1,
            "release_commit": commit,
            "canonical_manifest_sha256": manifest_sha256,
            "compose_profiles": selected_profiles,
            "images": selected_images,
            "release_toolchain": toolchain,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
