#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
API_DIR="$REPO_ROOT/api"

SEAWEEDFS_IMAGE=${SEAWEEDFS_IMAGE:-chrislusf/seaweedfs:4.29}
SEAWEEDFS_TEST_PORT=${SEAWEEDFS_TEST_PORT:-18333}
SEAWEEDFS_MASTER_PORT=${SEAWEEDFS_MASTER_PORT:-19333}
CONTAINER_NAME=${SEAWEEDFS_CONTAINER_NAME:-lectern-seaweedfs-integration}

cleanup() {
    status=$?
    trap - EXIT INT TERM
    if [ "$status" -ne 0 ]; then
        docker logs "$CONTAINER_NAME" 2>/dev/null || true
    fi
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
    exit "$status"
}
trap cleanup EXIT INT TERM

command -v docker >/dev/null 2>&1 || {
    echo "docker is required" >&2
    exit 1
}
command -v curl >/dev/null 2>&1 || {
    echo "curl is required" >&2
    exit 1
}
command -v uv >/dev/null 2>&1 || {
    echo "uv is required" >&2
    exit 1
}

docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

docker run --detach \
    --name "$CONTAINER_NAME" \
    --publish "127.0.0.1:${SEAWEEDFS_TEST_PORT}:8333" \
    --publish "127.0.0.1:${SEAWEEDFS_MASTER_PORT}:9333" \
    --volume "$REPO_ROOT/infra/docker/seaweedfs/s3.json:/etc/seaweedfs/s3.json:ro,Z" \
    "$SEAWEEDFS_IMAGE" \
    server \
    -dir=/data \
    -ip.bind=0.0.0.0 \
    -master.volumeSizeLimitMB=1024 \
    -s3 \
    -s3.config=/etc/seaweedfs/s3.json >/dev/null

attempt=0
until curl --silent --fail \
    "http://127.0.0.1:${SEAWEEDFS_MASTER_PORT}/cluster/healthz" >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 60 ]; then
        echo "SeaweedFS did not become healthy" >&2
        exit 1
    fi
    sleep 1
done

cd "$API_DIR"
SEAWEEDFS_INTEGRATION=1 \
SEAWEEDFS_TEST_ENDPOINT="127.0.0.1:${SEAWEEDFS_TEST_PORT}" \
SEAWEEDFS_TEST_ACCESS_KEY=minioadmin \
SEAWEEDFS_TEST_SECRET_KEY=minioadmin \
uv run pytest -m integration tests/integration/storage -ra "$@"
