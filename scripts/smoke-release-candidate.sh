#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: $0 <api|worker|web|selfhost-worker> <immutable-candidate-ref> <linux/amd64|linux/arm64>" >&2
  exit 64
fi

component=$1
candidate_ref=$2
platform=$3

case "$component" in
  api|worker|web|selfhost-worker) ;;
  *) echo "unsupported component: $component" >&2; exit 64 ;;
esac

[[ "$candidate_ref" =~ @sha256:[0-9a-f]{64}$ ]] || {
  echo "candidate must be an immutable digest reference: $candidate_ref" >&2
  exit 64
}
case "$platform" in
  linux/amd64|linux/arm64) ;;
  *) echo "unsupported platform: $platform" >&2; exit 64 ;;
esac

candidate_repository=${candidate_ref%@sha256:*}
os=${platform%/*}
arch=${platform#*/}
manifest_json=$(docker buildx imagetools inspect --raw "$candidate_ref")
child_digest=$(jq -r --arg os "$os" --arg arch "$arch" '
  [.manifests[] | select(.platform.os == $os and .platform.architecture == $arch) | .digest]
  | if length == 1 then .[0] else empty end
' <<<"$manifest_json")
[[ "$child_digest" =~ ^sha256:[0-9a-f]{64}$ ]] || {
  echo "could not resolve exactly one $platform child digest from $candidate_ref" >&2
  exit 1
}
candidate_ref="${candidate_repository}@${child_digest}"

echo "Runtime-smoke testing exact $component child $candidate_ref on $platform"

case "$component" in
  api)
    name="wikint-api-smoke-${RANDOM}-${RANDOM}"
    cleanup() {
      docker logs "$name" 2>/dev/null || true
      docker rm -f "$name" >/dev/null 2>&1 || true
    }
    trap cleanup EXIT

    # Preserve the image ENTRYPOINT and start the real ASGI server. Lifespan is
    # disabled here because live storage behavior is exercised in the required
    # SeaweedFS jobs; this gate is specifically proving that the exact packaged
    # image/architecture can run its launcher, imports and HTTP runtime.
    docker run -d --platform "$platform" --network host --name "$name" \
      -e DATABASE_URL=postgresql+asyncpg://release_smoke:release_smoke@127.0.0.1:5432/release_smoke \
      -e RUN_MIGRATIONS=false \
      "$candidate_ref" \
      uvicorn app.main:app --host 127.0.0.1 --port 18080 --lifespan off >/dev/null

    for _ in $(seq 1 45); do
      if curl --fail --silent http://127.0.0.1:18080/api/health >/dev/null; then
        break
      fi
      if ! docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null | grep -qx true; then
        echo "api candidate exited before becoming healthy" >&2
        exit 1
      fi
      sleep 1
    done
    curl --fail --silent http://127.0.0.1:18080/api/health >/dev/null

    # Exercise the native scanner import/initialization from the exact running
    # candidate too; API startup depends on this when lifespan is enabled.
    docker exec "$name" /venv/bin/python -c '
from app.core.security.scanner import MalwareScanner
scanner = MalwareScanner()
scanner.initialize()
print("api-candidate-runtime-ok")
'
    trap - EXIT
    docker rm -f "$name" >/dev/null
    ;;

  worker)
    # worker-start.sh has no external startup dependency, so overriding CMD while
    # preserving the image ENTRYPOINT exercises the real packaged launcher.
    docker run --rm --platform "$platform" \
      "$candidate_ref" \
      /venv/bin/python -c '
import shutil
import pikepdf
import yara
from app.workers.settings import WorkerSettings

required = ("bwrap", "ffmpeg", "gs", "soffice", "rsvg-convert", "exiftool")
missing = [name for name in required if shutil.which(name) is None]
assert not missing, f"missing worker runtime tools: {missing}"
assert WorkerSettings
assert pikepdf.__version__
assert yara.__version__
print("worker-candidate-runtime-ok")
'
    ;;

  web)
    name="wikint-web-smoke-${RANDOM}-${RANDOM}"
    cleanup() {
      docker logs "$name" 2>/dev/null || true
      docker rm -f "$name" >/dev/null 2>&1 || true
    }
    trap cleanup EXIT
    docker run -d --platform "$platform" --name "$name" "$candidate_ref" >/dev/null
    for _ in $(seq 1 30); do
      if docker exec "$name" wget --quiet --spider http://127.0.0.1/; then
        trap - EXIT
        docker rm -f "$name" >/dev/null
        echo "web-candidate-runtime-ok"
        exit 0
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
      -e WORKER_ZIP_HMAC_SECRET=runtime-smoke-only \
      -e S3_ENDPOINT=127.0.0.1:1 \
      "$candidate_ref" >/dev/null
    for _ in $(seq 1 30); do
      if docker exec "$name" wget --quiet --spider http://127.0.0.1:8788/healthz; then
        trap - EXIT
        docker rm -f "$name" >/dev/null
        echo "selfhost-worker-candidate-runtime-ok"
        exit 0
      fi
      sleep 1
    done
    echo "selfhost-worker candidate failed its runtime health check" >&2
    exit 1
    ;;
esac
