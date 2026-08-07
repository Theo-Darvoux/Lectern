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
candidate_digest="sha256:${BASH_REMATCH[2]}"
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

tag_ref="${release_repository}:${release_tag}"
inspect_out=$(mktemp)
inspect_err=$(mktemp)
metadata=$(mktemp)
trap 'rm -f "$inspect_out" "$inspect_err" "$metadata"' EXIT

inspect_digest() {
  local reference=$1
  docker buildx imagetools inspect \
    "$reference" \
    --format '{{.Manifest.Digest}}'
}

# Commit tags are write-once. A same-digest rerun is idempotent; a conflicting
# digest is a provenance violation and must never overwrite the existing tag.
if inspect_digest "$tag_ref" >"$inspect_out" 2>"$inspect_err"; then
  existing=$(tr -d '\r\n' <"$inspect_out")
  if [[ ! $existing =~ ^sha256:[0-9a-f]{64}$ ]]; then
    echo "existing commit tag returned an invalid digest" >&2
    exit 65
  fi
  if [[ $existing != "$candidate_digest" ]]; then
    echo "immutable release tag already exists with a different digest" >&2
    echo "tag:       $tag_ref" >&2
    echo "existing:  $existing" >&2
    echo "candidate: $candidate_digest" >&2
    exit 65
  fi
  printf '%s\n' "$existing"
  exit 0
else
  inspect_status=$?
fi

inspect_detail=$(cat "$inspect_err")
if ! grep -Eiq '(manifest unknown|name unknown|not found|404)' <<<"$inspect_detail"; then
  echo "could not safely determine whether immutable release tag exists" >&2
  [[ -n $inspect_detail ]] && printf '%s\n' "$inspect_detail" >&2
  exit "$inspect_status"
fi

# The repository-wide release concurrency group serializes this check/create
# sequence across main and alpha tag releases.
docker buildx imagetools create \
  --metadata-file "$metadata" \
  --tag "$tag_ref" \
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
if [[ $digest != "$candidate_digest" ]]; then
  echo "promoted digest does not equal candidate digest" >&2
  echo "candidate: $candidate_digest" >&2
  echo "promoted:  $digest" >&2
  exit 65
fi

# Verify the registry-visible tag after mutation before exposing the output.
post_digest=$(inspect_digest "$tag_ref")
post_digest=${post_digest//$'\r'/}
post_digest=${post_digest//$'\n'/}
if [[ $post_digest != "$candidate_digest" ]]; then
  echo "registry commit tag verification failed after promotion" >&2
  echo "expected: $candidate_digest" >&2
  echo "actual:   $post_digest" >&2
  exit 65
fi

printf '%s\n' "$candidate_digest"
