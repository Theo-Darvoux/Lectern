#!/usr/bin/env bash
set -euo pipefail

if (( $# != 2 )); then
  echo "usage: $0 <tested-seaweedfs-image> <approved-seaweedfs-image>" >&2
  exit 64
fi

tested=$1
approved=$2
pattern='^docker\.io/chrislusf/seaweedfs@sha256:[0-9a-f]{64}$'
if [[ ! $tested =~ $pattern ]]; then
  echo "tested SeaweedFS reference is not a canonical immutable digest" >&2
  exit 64
fi
if [[ ! $approved =~ $pattern ]]; then
  echo "approved SeaweedFS reference is not a canonical immutable digest" >&2
  exit 64
fi
if [[ $tested != "$approved" ]]; then
  echo "approved SeaweedFS digest does not match the digest exercised by the live suites" >&2
  echo "tested:   $tested" >&2
  echo "approved: $approved" >&2
  exit 65
fi
