#!/usr/bin/env bash
set -euo pipefail

if (( $# != 5 )); then
  echo "usage: $0 <alias> <api-ref> <worker-ref> <web-ref> <selfhost-worker-ref>" >&2
  exit 64
fi

alias_name=$1
shift
if [[ ! $alias_name =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]]; then
  echo "invalid release alias" >&2
  exit 64
fi
if [[ $alias_name =~ ^sha-[0-9a-f]{40}$ ]]; then
  echo "commit tags are not aliases" >&2
  exit 64
fi

echo "warning: cross-repository aliases are best-effort conveniences, not deployable release identity" >&2
expected_components=(api worker web selfhost-worker)
refs=("$@")
for index in "${!refs[@]}"; do
  component=${expected_components[$index]}
  reference=${refs[$index]}
  if [[ ! $reference =~ ^ghcr\.io/theo-darvoux/lectern/${component}-release@sha256:[0-9a-f]{64}$ ]]; then
    echo "invalid ${component} release reference" >&2
    exit 64
  fi
  # Preflight every source before mutating any alias.
  docker buildx imagetools inspect "$reference" --format '{{json .Manifest}}' >/dev/null
done

for index in "${!refs[@]}"; do
  component=${expected_components[$index]}
  reference=${refs[$index]}
  docker buildx imagetools create \
    --tag "ghcr.io/theo-darvoux/lectern/${component}-release:${alias_name}" \
    "$reference"
done
