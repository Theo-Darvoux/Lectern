#!/usr/bin/env bash
set -euo pipefail

if (( $# < 1 || $# > 2 )); then
  echo "usage: $0 <production-image-env-file> [output-directory]" >&2
  exit 64
fi

env_file=$1
output_dir=${2:-release-manifests}
[[ -f "$env_file" ]] || { echo "missing environment file: $env_file" >&2; exit 66; }
env_dir=$(CDPATH= cd -- "$(dirname -- "$env_file")" && pwd)
env_file="$env_dir/$(basename -- "$env_file")"
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_root"
commit=$(git rev-parse --verify HEAD)
stem="production-${commit}"
mkdir -p "$output_dir"
manifest="$output_dir/${stem}.json"
images="$output_dir/${stem}.compose-images.txt"
checksums="$output_dir/${stem}.sha256"

python3 scripts/write-production-release-manifest.py \
  --env-file "$env_file" \
  --output "$manifest" \
  --commit "$commit"

compose_env_args=()
if [[ -f .env ]]; then
  compose_env_args+=(--env-file .env)
fi
# The image-only file is last so runtime configuration cannot override reviewed digests.
compose_env_args+=(--env-file "$env_file")
docker compose "${compose_env_args[@]}" -f compose.yaml -f compose.prod.yaml config --quiet
temporary=$(mktemp "$output_dir/.${stem}.images.XXXXXX")
trap 'rm -f "$temporary"' EXIT
docker compose "${compose_env_args[@]}" -f compose.yaml -f compose.prod.yaml config --images \
  | LC_ALL=C sort -u > "$temporary"
mv "$temporary" "$images"
trap - EXIT

(
  cd "$output_dir"
  sha256sum "$(basename "$manifest")" "$(basename "$images")" > "$(basename "$checksums")"
)
printf 'Wrote %s\nWrote %s\nWrote %s\n' "$manifest" "$images" "$checksums"
