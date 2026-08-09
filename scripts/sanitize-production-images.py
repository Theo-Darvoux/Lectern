#!/usr/bin/env python3
"""Validate and canonicalize a production image-only environment file."""

from __future__ import annotations

import argparse
from pathlib import Path

from release_manifest_lib import (
    atomic_write_text,
    canonical_env_text,
    parse_env_file,
    validate_release_values,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()

    try:
        values = parse_env_file(args.env_file)
        commit, profiles, images = validate_release_values(
            values, expected_commit=args.commit
        )
        atomic_write_text(args.output, canonical_env_text(commit, profiles, images))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
