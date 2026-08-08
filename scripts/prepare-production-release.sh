#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
usage: prepare-production-release.sh \
  --canonical-manifest <production-commit.json> \
  --runtime-env <runtime.env> \
  [--profiles <comma-separated-profiles>] \
  [--output-directory <directory>]

The canonical GitHub Actions release manifest is the only image-trust input.
Runtime secrets are used only for non-outputting Compose validation and are never
copied into generated release/deployment artifacts.
EOF
}

canonical_manifest=
runtime_env=
profiles=
profiles_set=0
output_dir=release-manifests

while (( $# )); do
  case "$1" in
    --canonical-manifest)
      (( $# >= 2 )) || { usage; exit 64; }
      canonical_manifest=$2
      shift 2
      ;;
    --runtime-env)
      (( $# >= 2 )) || { usage; exit 64; }
      runtime_env=$2
      shift 2
      ;;
    --profiles)
      (( $# >= 2 )) || { usage; exit 64; }
      profiles=$2
      profiles_set=1
      shift 2
      ;;
    --output-directory)
      (( $# >= 2 )) || { usage; exit 64; }
      output_dir=$2
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unsupported argument: $1" >&2
      usage
      exit 64
      ;;
  esac
done

[[ -n $canonical_manifest && -n $runtime_env ]] || { usage; exit 64; }
[[ -f $canonical_manifest ]] || { echo "missing canonical manifest: $canonical_manifest" >&2; exit 66; }
[[ -f $runtime_env ]] || { echo "missing runtime environment file: $runtime_env" >&2; exit 66; }

canonical_manifest=$(realpath -- "$canonical_manifest")
runtime_env=$(realpath -- "$runtime_env")
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_root"

# Deployment preparation may not certify a commit while tracked files differ.
git diff --quiet -- . || { echo "tracked worktree changes must be committed before deployment preparation" >&2; exit 65; }
git diff --cached --quiet -- . || { echo "staged changes must be committed before deployment preparation" >&2; exit 65; }
commit=$(git rev-parse --verify HEAD)

mkdir -p "$output_dir"
output_dir=$(realpath -- "$output_dir")
stem="production-${commit}"
canonical_copy="$output_dir/${stem}.canonical.json"
deployment_env="$output_dir/${stem}.deployment-images.env"
deployment_metadata="$output_dir/${stem}.deployment-selection.json"
inspection="$output_dir/${stem}.registry-inspection.json"
service_map="$output_dir/${stem}.compose-services.json"
checksums="$output_dir/${stem}.deployment.sha256"
temporary_compose=$(mktemp "${TMPDIR:-/tmp}/${stem}.compose.XXXXXX.json")
trap 'rm -f "$temporary_compose"' EXIT

if [[ $canonical_manifest != "$canonical_copy" ]]; then
  cp -- "$canonical_manifest" "$canonical_copy"
fi

materialize=(
  python3 scripts/materialize-production-deployment.py
  --manifest "$canonical_copy"
  --commit "$commit"
  --env-output "$deployment_env"
  --metadata-output "$deployment_metadata"
)
if (( profiles_set )); then
  materialize+=(--profiles "$profiles")
fi
"${materialize[@]}"

# Re-check registry availability and immutable workload commit-tag binding. This
# verifies the canonical choices still exist; it never accepts replacement image
# digests from local operator input.
python3 scripts/inspect-production-images.py \
  --env-file "$deployment_env" \
  --output "$inspection" \
  --commit "$commit"

profile_args=()
selected_profiles=$(sed -n 's/^COMPOSE_PROFILES=//p' "$deployment_env")
IFS=',' read -r -a profile_values <<< "$selected_profiles"
for profile in "${profile_values[@]}"; do
  [[ -n $profile ]] && profile_args+=(--profile "$profile")
done

# Host variables are removed so they cannot override the canonical image file.
# RUNTIME_ENV_FILE makes service-level env_file references point at the explicit
# operator runtime file instead of a repository-local .env.
clean_env=(env -i "PATH=$PATH" "RUNTIME_ENV_FILE=$runtime_env")
[[ -n ${HOME:-} ]] && clean_env+=("HOME=$HOME")
[[ -n ${DOCKER_CONFIG:-} ]] && clean_env+=("DOCKER_CONFIG=$DOCKER_CONFIG")
[[ -n ${XDG_RUNTIME_DIR:-} ]] && clean_env+=("XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR")
[[ -n ${TMPDIR:-} ]] && clean_env+=("TMPDIR=$TMPDIR")

runtime_compose=(
  docker compose
  --env-file "$runtime_env"
  --env-file "$deployment_env"
  -f compose.yaml
  -f compose.prod.yaml
  "${profile_args[@]}"
)

# The real runtime environment is deliberately used only for a quiet validation.
# --no-env-resolution prevents Compose from materializing service env-file values.
"${clean_env[@]}" "${runtime_compose[@]}" config --quiet --no-env-resolution

# Persist only a synthetic, non-secret structural rendering. Even this rendering
# is temporary; the artifact retains only the minimized service→image evidence.
synthetic_runtime="$repo_root/.env.example"
synthetic_env=(env -i "PATH=$PATH" "RUNTIME_ENV_FILE=$synthetic_runtime")
[[ -n ${HOME:-} ]] && synthetic_env+=("HOME=$HOME")
[[ -n ${DOCKER_CONFIG:-} ]] && synthetic_env+=("DOCKER_CONFIG=$DOCKER_CONFIG")
[[ -n ${XDG_RUNTIME_DIR:-} ]] && synthetic_env+=("XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR")
[[ -n ${TMPDIR:-} ]] && synthetic_env+=("TMPDIR=$TMPDIR")
synthetic_compose=(
  docker compose
  --env-file "$synthetic_runtime"
  --env-file "$deployment_env"
  -f compose.yaml
  -f compose.prod.yaml
  "${profile_args[@]}"
)
"${synthetic_env[@]}" "${synthetic_compose[@]}" config \
  --format json --no-env-resolution > "$temporary_compose"

python3 scripts/validate-production-compose.py \
  --env-file "$deployment_env" \
  --compose-config "$temporary_compose" \
  --output "$service_map" \
  --commit "$commit"

(
  cd "$output_dir"
  sha256sum \
    "$(basename "$canonical_copy")" \
    "$(basename "$deployment_env")" \
    "$(basename "$deployment_metadata")" \
    "$(basename "$inspection")" \
    "$(basename "$service_map")" \
    > "$(basename "$checksums")"
)

printf 'Validated canonical release %s\n' "$commit"
printf 'Wrote %s\nWrote %s\nWrote %s\nWrote %s\nWrote %s\nWrote %s\n' \
  "$canonical_copy" \
  "$deployment_env" \
  "$deployment_metadata" \
  "$inspection" \
  "$service_map" \
  "$checksums"
