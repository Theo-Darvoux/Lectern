#!/usr/bin/env python3
"""Certify the exact production Compose service→image mapping."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from release_manifest_lib import (
    COMPOSE_SERVICE_MAP_SCHEMA_VERSION,
    atomic_write_json,
    expected_compose_service_images,
    parse_env_file,
    sha256_file,
    validate_release_values,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--compose-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()

    try:
        values = parse_env_file(args.env_file)
        commit, profiles, images = validate_release_values(values, expected_commit=args.commit)
        payload = json.loads(args.compose_config.read_text(encoding="utf-8"))
        services = payload.get("services")
        if not isinstance(services, dict):
            raise ValueError("Compose JSON does not contain a services object")

        actual: dict[str, str] = {}
        for service_name, service in services.items():
            if not isinstance(service_name, str) or not isinstance(service, dict):
                raise ValueError("Compose services contain an invalid entry")
            image = service.get("image")
            if image is None:
                continue
            if not isinstance(image, str) or not image:
                raise ValueError(f"Compose service {service_name} has an invalid image")
            actual[service_name] = image
        actual = dict(sorted(actual.items()))
        expected = expected_compose_service_images(images)
        if actual != expected:
            missing = sorted(set(expected) - set(actual))
            unexpected = sorted(set(actual) - set(expected))
            mismatched = sorted(
                name
                for name in set(expected) & set(actual)
                if expected[name] != actual[name]
            )
            details: list[str] = []
            if missing:
                details.append("missing services: " + ", ".join(missing))
            if unexpected:
                details.append("unexpected image-bearing services: " + ", ".join(unexpected))
            for name in mismatched:
                details.append(
                    f"{name}: expected {expected[name]}, got {actual[name]}"
                )
            raise ValueError(
                "Compose service→image mapping does not match release policy ("
                + "; ".join(details)
                + ")"
            )

        atomic_write_json(
            args.output,
            {
                "schema_version": COMPOSE_SERVICE_MAP_SCHEMA_VERSION,
                "release_commit": commit,
                "compose_profiles": profiles,
                "release_input_sha256": sha256_file(args.env_file),
                "services": actual,
            },
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
