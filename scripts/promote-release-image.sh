#!/usr/bin/env bash
set -euo pipefail

if (( $# != 3 )); then
  echo "usage: $0 <candidate-ref> <release-repository> <release-tag>" >&2
  exit 64
fi

candidate_ref=$1
release_repository=$2
release_tag=$3

if [[ ! $candidate_ref =~ ^ghcr\.io/theo-darvoux/lectern/(api|worker|web|selfhost-worker)-candidate@sha256:([0-9a-f]{64})$ ]]; then
  echo "candidate reference must identify an approved candidate manifest digest" >&2
  exit 64
fi
component=${BASH_REMATCH[1]}
expected_release="ghcr.io/theo-darvoux/lectern/${component}-release"

if [[ $release_repository != "$expected_release" ]]; then
  echo "candidate/release repository mismatch" >&2
  exit 64
fi
if [[ ! $release_tag =~ ^sha-[0-9a-f]{40}$ ]]; then
  echo "release tag must be an immutable Git commit tag" >&2
  exit 64
fi
if [[ -n ${GITHUB_SHA:-} && $release_tag != "sha-${GITHUB_SHA}" ]]; then
  echo "release tag does not match GITHUB_SHA" >&2
  exit 64
fi

metadata=$(mktemp)
trap 'rm -f "$metadata"' EXIT
# This phase may publish only the immutable commit tag. Mutable aliases are
# deliberately reserved for the aggregate post-manifest job.
docker buildx imagetools create \
  --metadata-file "$metadata" \
  --tag "${release_repository}:${release_tag}" \
  "$candidate_ref" >&2

digest=$(python3 - "$metadata" <<'PY'
import json
import re
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    payload = json.load(stream)
digest = payload.get("containerimage.descriptor", {}).get("digest", "")
if re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
    raise SystemExit("imagetools did not return a valid promoted digest")
print(digest)
PY
)
printf '%s\n' "$digest"
