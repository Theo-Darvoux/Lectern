#!/usr/bin/env python3
"""Require Docker Compose to resolve exactly the images recorded in the manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from release_manifest_lib import expected_compose_images


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--compose-images", type=Path, required=True)
    args = parser.parse_args()

    try:
        payload = json.loads(args.manifest.read_text(encoding="utf-8"))
        images = payload.get("images")
        if not isinstance(images, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in images.items()
        ):
            raise ValueError("manifest does not contain a valid image map")
        expected = expected_compose_images(images)
        actual = {
            line.strip()
            for line in args.compose_images.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        if actual != expected:
            missing = sorted(expected - actual)
            unexpected = sorted(actual - expected)
            details: list[str] = []
            if missing:
                details.append("missing: " + ", ".join(missing))
            if unexpected:
                details.append("unexpected: " + ", ".join(unexpected))
            raise ValueError("Compose image set does not match release manifest (" + "; ".join(details) + ")")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
