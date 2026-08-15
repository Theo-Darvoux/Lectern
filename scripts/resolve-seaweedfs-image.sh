#!/usr/bin/env bash
set -euo pipefail

if (( $# != 1 )); then
  echo "usage: $0 <seaweedfs-image>" >&2
  exit 64
fi

source_image=$1
if [[ ! $source_image =~ ^(docker\.io/)?chrislusf/seaweedfs(:[^/@[:space:]]+|@sha256:[0-9a-f]{64})$ ]]; then
  echo "SeaweedFS source must be chrislusf/seaweedfs with an explicit tag or digest" >&2
  exit 64
fi

# Resolve the registry manifest directly. Do not depend on docker image
# RepoDigests naming, which may return either short or fully-qualified names.
raw_digest=$(docker buildx imagetools inspect \
  "$source_image" \
  --format '{{.Manifest.Digest}}')
raw_digest=${raw_digest//$'\r'/}
raw_digest=${raw_digest//$'\n'/}
# Test doubles and older tooling may return repo@digest; normalize either form.
digest=${raw_digest##*@}
if [[ ! $digest =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "registry did not return a valid SeaweedFS manifest digest: $raw_digest" >&2
  exit 65
fi

printf 'docker.io/chrislusf/seaweedfs@%s\n' "$digest"
