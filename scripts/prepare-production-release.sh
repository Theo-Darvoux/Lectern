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

# A release may not claim a commit while validating modified tracked files.
git diff --quiet -- . || { echo "tracked worktree changes must be committed before release preparation" >&2; exit 65; }
git diff --cached --quiet -- . || { echo "staged changes must be committed before release preparation" >&2; exit 65; }
commit=$(git rev-parse --verify HEAD)

stem="production-${commit}"
mkdir -p "$output_dir"
manifest="$output_dir/${stem}.json"
images="$output_dir/${stem}.compose-images.txt"
inspection="$output_dir/${stem}.registry-inspection.json"
checksums="$output_dir/${stem}.sha256"

sanitized=$(mktemp "${TMPDIR:-/tmp}/${stem}.env.XXXXXX")
temporary_images=$(mktemp "$output_dir/.${stem}.images.XXXXXX")
trap 'rm -f "$sanitized" "$temporary_images"' EXIT

python3 scripts/sanitize-production-images.py \
  --env-file "$env_file" \
  --output "$sanitized" \
  --commit "$commit"

# Registry verification proves existence, binds workload sha-<commit> tags to
# their digests, and requires exactly amd64+arm64 for every workload image.
python3 scripts/inspect-production-images.py \
  --env-file "$sanitized" \
  --output "$inspection" \
  --commit "$commit"

python3 scripts/write-production-release-manifest.py \
  --env-file "$sanitized" \
  --inspection-file "$inspection" \
  --output "$manifest" \
  --commit "$commit"

compose_env_args=()
if [[ -f .env ]]; then
  compose_env_args+=(--env-file .env)
fi
compose_env_args+=(--env-file "$sanitized")
profile_args=()
profiles=$(sed -n 's/^COMPOSE_PROFILES=//p' "$sanitized")
IFS=',' read -r -a profile_values <<< "$profiles"
for profile in "${profile_values[@]}"; do
  [[ -n "$profile" ]] && profile_args+=(--profile "$profile")
done

# Host variables are deliberately removed so they cannot outrank the reviewed
# image file during Compose interpolation. Only Docker client plumbing remains.
clean_env=(env -i "PATH=$PATH")
[[ -n ${HOME:-} ]] && clean_env+=("HOME=$HOME")
[[ -n ${DOCKER_CONFIG:-} ]] && clean_env+=("DOCKER_CONFIG=$DOCKER_CONFIG")
[[ -n ${XDG_RUNTIME_DIR:-} ]] && clean_env+=("XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR")
[[ -n ${TMPDIR:-} ]] && clean_env+=("TMPDIR=$TMPDIR")

"${clean_env[@]}" docker compose \
  "${compose_env_args[@]}" \
  -f compose.yaml -f compose.prod.yaml \
  "${profile_args[@]}" \
  config --quiet

"${clean_env[@]}" docker compose \
  "${compose_env_args[@]}" \
  -f compose.yaml -f compose.prod.yaml \
  "${profile_args[@]}" \
  config --images | LC_ALL=C sort -u > "$temporary_images"

python3 scripts/validate-production-compose.py \
  --manifest "$manifest" \
  --compose-images "$temporary_images"

mv "$temporary_images" "$images"
trap 'rm -f "$sanitized"' EXIT
(
  cd "$output_dir"
  sha256sum \
    "$(basename "$manifest")" \
    "$(basename "$images")" \
    "$(basename "$inspection")" \
    > "$(basename "$checksums")"
)
printf 'Wrote %s\nWrote %s\nWrote %s\nWrote %s\n' \
  "$manifest" "$images" "$inspection" "$checksums"
