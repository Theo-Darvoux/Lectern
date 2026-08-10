#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
API_DIR="$REPO_ROOT/api"

REDIS_TEST_IMAGE=${REDIS_TEST_IMAGE:-docker.io/library/redis:7.4-alpine}
REDIS_CONTAINER_NAME=${REDIS_CONTAINER_NAME:-lectern-redis-atomicity-test}
STARTED_REDIS=0

cleanup() {
    status=$?
    trap - EXIT INT TERM
    if [ "$status" -ne 0 ] && [ "$STARTED_REDIS" -eq 1 ]; then
        docker logs "$REDIS_CONTAINER_NAME" 2>/dev/null || true
    fi
    if [ "$STARTED_REDIS" -eq 1 ]; then
        docker rm -f "$REDIS_CONTAINER_NAME" >/dev/null 2>&1 || true
    fi
    exit "$status"
}
trap cleanup EXIT INT TERM

# Process script options if specific modes requested
TEST_TARGETS=""
PYTEST_ARGS=""

for arg in "$@"; do
    case "$arg" in
        --auth-only)
            TEST_TARGETS="tests/integration/test_auth_redis_atomicity.py"
            ;;
        --storage-only)
            TEST_TARGETS="tests/integration/test_storage_redis_atomicity.py"
            ;;
        --unit-auth)
            TEST_TARGETS="tests/test_auth.py tests/test_auth_config.py tests/test_auth_google.py tests/test_auth_lifecycle_hardening.py tests/test_magic_links.py"
            ;;
        *)
            PYTEST_ARGS="$PYTEST_ARGS $arg"
            ;;
    esac
done

if [ -z "$TEST_TARGETS" ]; then
    TEST_TARGETS="tests/integration/test_auth_redis_atomicity.py tests/integration/test_storage_redis_atomicity.py"
fi

# Ensure commands are available
command -v uv >/dev/null 2>&1 || {
    echo "uv is required to run tests" >&2
    exit 1
}

TARGET_REDIS_URL="${AUTH_ATOMICITY_REDIS_URL:-${REDIS_URL:-}}"

if [ -z "$TARGET_REDIS_URL" ]; then
    command -v docker >/dev/null 2>&1 || {
        echo "docker is required to launch disposable Redis, or set AUTH_ATOMICITY_REDIS_URL" >&2
        exit 1
    }

    echo "Starting disposable Redis container ($REDIS_CONTAINER_NAME)..."
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

    TARGET_REDIS_URL="redis://127.0.0.1:${REDIS_HOST_PORT}/15"
    echo "Using disposable Redis at $TARGET_REDIS_URL"
else
    echo "Using existing Redis at $TARGET_REDIS_URL"
fi

cd "$API_DIR"
echo "Running Redis atomicity & auth integration tests..."
# shellcheck disable=SC2086
AUTH_ATOMICITY_REDIS_URL="$TARGET_REDIS_URL" \
REDIS_URL="$TARGET_REDIS_URL" \
uv run pytest -m integration $TEST_TARGETS -ra $PYTEST_ARGS
