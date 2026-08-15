#!/usr/bin/env python3
"""Validate and export repository-pinned release-control-plane inputs."""

from __future__ import annotations

import argparse
from pathlib import Path

from release_manifest_lib import RELEASE_TOOLCHAIN_KEYS, parse_release_toolchain

_OUTPUT_NAMES = {key: key.lower() for key in RELEASE_TOOLCHAIN_KEYS}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--toolchain-file",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "deploy/release-toolchain.env",
    )
    parser.add_argument("--github-env", type=Path)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()

    try:
        values = parse_release_toolchain(args.toolchain_file)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    text = "".join(f"{key}={values[key]}\n" for key in RELEASE_TOOLCHAIN_KEYS)
    if args.github_env is None and args.github_output is None:
        print(text, end="")
    if args.github_env is not None:
        with args.github_env.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    if args.github_output is not None:
        with args.github_output.open("a", encoding="utf-8", newline="\n") as stream:
            for key in RELEASE_TOOLCHAIN_KEYS:
                stream.write(f"{_OUTPUT_NAMES[key]}={values[key]}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
