#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
API_DIR="$REPO_ROOT/api"

SEAWEEDFS_IMAGE=${SEAWEEDFS_IMAGE:-chrislusf/seaweedfs:4.29}
SEAWEEDFS_TEST_PORT=${SEAWEEDFS_TEST_PORT:-18333}
SEAWEEDFS_MASTER_PORT=${SEAWEEDFS_MASTER_PORT:-19333}
CONTAINER_NAME=${SEAWEEDFS_CONTAINER_NAME:-lectern-seaweedfs-integration}

REDIS_TEST_IMAGE=${SEAWEEDFS_REDIS_IMAGE:-docker.io/library/redis:7.4-alpine@sha256:e7723ff73d963f5cc6d9c4643ea3d989527a402a319239054e9472a7fb9219a2}
REDIS_CONTAINER_NAME=${SEAWEEDFS_REDIS_CONTAINER_NAME:-lectern-redis-seaweedfs-integration}
STARTED_REDIS=0

cleanup() {
    status=$?
    trap - EXIT INT TERM
    if [ "$status" -ne 0 ]; then
        docker logs "$CONTAINER_NAME" 2>/dev/null || true
        if [ "$STARTED_REDIS" -eq 1 ]; then
            docker logs "$REDIS_CONTAINER_NAME" 2>/dev/null || true
        fi
    fi
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
    if [ "$STARTED_REDIS" -eq 1 ]; then
        docker rm -f "$REDIS_CONTAINER_NAME" >/dev/null 2>&1 || true
    fi
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

if [ -z "${REDIS_URL:-}" ]; then
    docker rm -f "$REDIS_CONTAINER_NAME" >/dev/null 2>&1 || true
    docker run --detach \
        --name "$REDIS_CONTAINER_NAME" \
        --publish "127.0.0.1::6379" \
        "$REDIS_TEST_IMAGE" \
        redis-server \
        --appendonly yes \
        --appendfsync always \
        --save "" >/dev/null
    STARTED_REDIS=1

    attempt=0
    REDIS_HOST_PORT=""
    until [ -n "$REDIS_HOST_PORT" ]; do
        REDIS_HOST_PORT=$(
            docker port "$REDIS_CONTAINER_NAME" 6379/tcp 2>/dev/null \
                | sed -n '1{s/.*://;p;}'
        )
        if [ -n "$REDIS_HOST_PORT" ]; then
            break
        fi
        attempt=$((attempt + 1))
        if [ "$attempt" -ge 30 ]; then
            echo "Disposable Redis did not publish a host port" >&2
            exit 1
        fi
        sleep 1
    done

    attempt=0
    until docker exec "$REDIS_CONTAINER_NAME" redis-cli ping 2>/dev/null \
        | grep -qx PONG; do
        attempt=$((attempt + 1))
        if [ "$attempt" -ge 60 ]; then
            echo "Disposable Redis did not become healthy" >&2
            exit 1
        fi
        sleep 1
    done

    REDIS_URL="redis://127.0.0.1:${REDIS_HOST_PORT}/14"
    export REDIS_URL
    echo "Using disposable Redis at $REDIS_URL"
else
    export REDIS_URL
    echo "Using caller-provided Redis at $REDIS_URL"
fi

cd "$API_DIR"
uv run python - <<'PYREDIS'
import asyncio
import os
import sys

from redis.asyncio import Redis
from redis.exceptions import RedisError


async def main() -> None:
    redis = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    try:
        await redis.ping()
        await redis.config_set("appendonly", "yes")
        await redis.config_set("appendfsync", "always")
        await redis.flushdb()
        await redis.set("storage:ci:aof-probe", "1")
        result = await redis.execute_command("WAITAOF", 1, 0, 5000)
        if (
            not isinstance(result, (list, tuple))
            or len(result) != 2
            or int(result[0]) < 1
        ):
            raise RuntimeError(f"Redis AOF durability unavailable: {result!r}")
        await redis.delete("storage:ci:aof-probe")
    except (RedisError, OSError, RuntimeError) as exc:
        print(
            f"Redis at {os.environ['REDIS_URL']!r} is not usable for CAS "
            f"durability tests: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc
    finally:
        await redis.aclose()


asyncio.run(main())
PYREDIS

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
REDIS_URL="$REDIS_URL" \
uv run pytest -m integration tests/integration/storage -ra "$@"
