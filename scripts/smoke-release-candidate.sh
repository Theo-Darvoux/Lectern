#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: $0 <api|worker|web|selfhost-worker> <immutable-candidate-ref> <linux/amd64|linux/arm64>" >&2
  exit 64
fi

component=$1
candidate_index_ref=$2
platform=$3

case "$component" in
  api|worker|web|selfhost-worker) ;;
  *) echo "unsupported component: $component" >&2; exit 64 ;;
esac

[[ "$candidate_index_ref" =~ @sha256:[0-9a-f]{64}$ ]] || {
  echo "candidate must be an immutable digest reference: $candidate_index_ref" >&2
  exit 64
}
case "$platform" in
  linux/amd64|linux/arm64) ;;
  *) echo "unsupported platform: $platform" >&2; exit 64 ;;
esac

candidate_repository=${candidate_index_ref%@sha256:*}
os=${platform%/*}
arch=${platform#*/}
manifest_json=$(docker buildx imagetools inspect --raw "$candidate_index_ref")
child_digest=$(jq -r --arg os "$os" --arg arch "$arch" '
  [.manifests[] | select(.platform.os == $os and .platform.architecture == $arch) | .digest]
  | if length == 1 then .[0] else empty end
' <<<"$manifest_json")
[[ "$child_digest" =~ ^sha256:[0-9a-f]{64}$ ]] || {
  echo "could not resolve exactly one $platform child digest from $candidate_index_ref" >&2
  exit 1
}
candidate_ref="${candidate_repository}@${child_digest}"

echo "Runtime-smoke testing exact $component child $candidate_ref on $platform"

production_env=(
  -e ENVIRONMENT=production
  -e SECRET_KEY=runtime-smoke-secret-key-that-is-long-and-not-a-placeholder
  -e MEILI_MASTER_KEY=runtime-smoke-meili-master-key
  -e EUROOFFICE_JWT_SECRET=runtime-smoke-eurooffice-jwt-secret
  -e EUROOFFICE_FILE_TOKEN_SECRET=runtime-smoke-eurooffice-file-secret
  -e DATABASE_URL=postgresql+asyncpg://release_smoke:release_smoke@127.0.0.1:5432/release_smoke
  -e REDIS_URL=redis://127.0.0.1:6379/0
  -e MEILI_URL=http://127.0.0.1:1
  -e STORAGE_BACKEND=seaweedfs
  -e S3_ENDPOINT=127.0.0.1:1
  -e S3_ACCESS_KEY=runtime-smoke
  -e S3_SECRET_KEY=runtime-smoke
  -e S3_BUCKET=runtime-smoke
  -e S3_USE_SSL=false
)

# Python startup is substantially slower when an arm64 image is executed via
# QEMU on an x86_64 hosted runner. Keep native smoke tests strict while giving
# emulated API/worker startup enough time to complete their full lifespans.
python_startup_attempts=90
if [[ "$platform" == linux/arm64 && "$(uname -m)" == x86_64 ]]; then
  python_startup_attempts=180
fi

case "$component" in
  api)
    name="wikint-api-smoke-${RANDOM}-${RANDOM}"
    cleanup() {
      docker logs "$name" 2>/dev/null || true
      docker rm -f "$name" >/dev/null 2>&1 || true
    }
    trap cleanup EXIT

    # Preserve ENTRYPOINT+CMD and full FastAPI lifespan. /api/health can only
    # report "ok" after Redis/ARQ startup has completed.
    docker run -d --platform "$platform" --network host --name "$name" \
      "${production_env[@]}" \
      -e RUN_MIGRATIONS=false \
      "$candidate_ref" >/dev/null

    healthy=0
    for _ in $(seq 1 "$python_startup_attempts"); do
      if ! docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null | grep -qx true; then
        echo "api candidate exited before completing production startup" >&2
        exit 1
      fi

      body=$(curl --silent --fail http://127.0.0.1:8000/api/health 2>/dev/null || true)
      if [[ -n "$body" ]] && python3 -c \
        'import json,sys; p=json.load(sys.stdin); raise SystemExit(0 if p.get("status") == "ok" else 1)' \
        <<<"$body"; then
        healthy=1
        break
      fi
      sleep 1
    done
    [[ "$healthy" == 1 ]] || {
      echo "api candidate never reached status=ok with full lifespan enabled" >&2
      exit 1
    }

    docker exec "$name" /bin/sh -c \
      'tr "\000" " " </proc/1/cmdline | grep -q "uvicorn app.main:app"'

    trap - EXIT
    docker rm -f "$name" >/dev/null
    echo "api-candidate-production-startup-ok"
    ;;

  worker)
    name="wikint-worker-smoke-${RANDOM}-${RANDOM}"
    cleanup() {
      docker logs "$name" 2>/dev/null || true
      docker rm -f "$name" >/dev/null 2>&1 || true
    }
    trap cleanup EXIT

    # Preserve ENTRYPOINT+CMD. The marker is emitted only after
    # WorkerSettings.startup() has initialized its hard requirements.
    docker run -d --platform "$platform" --network host --name "$name" \
      "${production_env[@]}" \
      "$candidate_ref" >/dev/null

    started=0
    for _ in $(seq 1 "$python_startup_attempts"); do
      if ! docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null | grep -qx true; then
        echo "worker candidate exited before completing production startup" >&2
        exit 1
      fi
      if docker logs "$name" 2>&1 | grep -q "Worker startup complete"; then
        started=1
        break
      fi
      sleep 1
    done
    [[ "$started" == 1 ]] || {
      echo "worker candidate never completed WorkerSettings.startup()" >&2
      exit 1
    }

    docker exec "$name" /bin/sh -c \
      'tr "\000" " " </proc/1/cmdline | grep -q "arq.*app.workers.settings.WorkerSettings"'

    sleep 3
    docker inspect -f '{{.State.Running}}' "$name" | grep -qx true

    trap - EXIT
    docker rm -f "$name" >/dev/null
    echo "worker-candidate-production-startup-ok"
    ;;

  web)
    name="wikint-web-smoke-${RANDOM}-${RANDOM}"
    cleanup() {
      docker logs "$name" 2>/dev/null || true
      docker rm -f "$name" >/dev/null 2>&1 || true
    }
    trap cleanup EXIT
    docker run -d --platform "$platform" --name "$name" "$candidate_ref" >/dev/null
    for _ in $(seq 1 45); do
      if docker exec "$name" wget --quiet --spider http://127.0.0.1/; then
        trap - EXIT
        docker rm -f "$name" >/dev/null
        echo "web-candidate-runtime-ok"
        exit 0
      fi
      if ! docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null | grep -qx true; then
        echo "web candidate exited before becoming healthy" >&2
        exit 1
      fi
      sleep 1
    done
    echo "web candidate failed its runtime health check" >&2
    exit 1
    ;;

  selfhost-worker)
    name="wikint-delivery-smoke-${RANDOM}-${RANDOM}"
    cleanup() {
      docker logs "$name" 2>/dev/null || true
      docker rm -f "$name" >/dev/null 2>&1 || true
    }
    trap cleanup EXIT
    docker run -d --platform "$platform" --name "$name" \
      -e WORKER_ZIP_HMAC_SECRET=runtime-smoke-selfhost-worker-hmac-secret \
      -e S3_ENDPOINT=127.0.0.1:1 \
      "$candidate_ref" >/dev/null
    for _ in $(seq 1 45); do
      if docker exec "$name" wget --quiet --spider http://127.0.0.1:8788/healthz; then
        trap - EXIT
        docker rm -f "$name" >/dev/null
        echo "selfhost-worker-candidate-runtime-ok"
        exit 0
      fi
      if ! docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null | grep -qx true; then
        echo "selfhost-worker candidate exited before becoming healthy" >&2
        exit 1
      fi
      sleep 1
    done
    echo "selfhost-worker candidate failed its runtime health check" >&2
    exit 1
    ;;
esac
