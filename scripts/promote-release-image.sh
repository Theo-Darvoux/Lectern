#!/usr/bin/env bash
set -euo pipefail

if (( $# < 3 || $# > 4 )); then
  echo "usage: $0 <candidate-ref> <release-repository> <release-tag> [alias]" >&2
  exit 64
fi

candidate_ref=$1
release_repository=$2
release_tag=$3
alias_name=${4:-}

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
if [[ -n $alias_name && ! $alias_name =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]]; then
  echo "invalid release alias" >&2
  exit 64
fi

docker buildx imagetools create --tag "${release_repository}:${release_tag}" "$candidate_ref"
if [[ -n $alias_name && $alias_name != "$release_tag" ]]; then
  docker buildx imagetools create --tag "${release_repository}:${alias_name}" "${release_repository}:${release_tag}"
fi
