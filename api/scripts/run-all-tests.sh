#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
API_DIR="$REPO_ROOT/api"
WEB_DIR="$REPO_ROOT/web"

RUN_HERMETIC=1
RUN_REDIS=1
RUN_SEAWEEDFS=1
RUN_WEB=1

for arg in "$@"; do
    case "$arg" in
        --skip-seaweedfs)
            RUN_SEAWEEDFS=0
            ;;
        --skip-redis)
            RUN_REDIS=0
            ;;
        --skip-web)
            RUN_WEB=0
            ;;
        --unit-only)
            RUN_REDIS=0
            RUN_SEAWEEDFS=0
            RUN_WEB=0
            ;;
        *)
            ;;
    esac
done

command -v uv >/dev/null 2>&1 || {
    echo "uv is required to run tests" >&2
    exit 1
}

echo "=========================================="
echo " Starting WikINT Full Test Suite Run"
echo "=========================================="
echo ""

# Step 1: Hermetic Unit Tests
if [ "$RUN_HERMETIC" -eq 1 ]; then
    echo "------------------------------------------"
    echo "[1/4] Running Hermetic API Unit Tests..."
    echo "------------------------------------------"
    cd "$API_DIR"
    uv run pytest -m "not integration" -ra
    echo "✓ Hermetic unit tests passed."
    echo ""
fi

# Step 2: Redis Atomicity & Auth Integration Tests
if [ "$RUN_REDIS" -eq 1 ]; then
    echo "------------------------------------------"
    echo "[2/4] Running Redis Atomicity & Auth Integration Tests..."
    echo "------------------------------------------"
    "$SCRIPT_DIR/run-redis-atomicity-tests.sh"
    echo "✓ Redis atomicity & auth integration tests passed."
    echo ""
fi

# Step 3: SeaweedFS Integration & Topology Tests
if [ "$RUN_SEAWEEDFS" -eq 1 ]; then
    echo "------------------------------------------"
    echo "[3/4] Running SeaweedFS S3 Integration Tests..."
    echo "------------------------------------------"
    if command -v docker >/dev/null 2>&1; then
        "$SCRIPT_DIR/run-seaweedfs-integration-tests.sh"
        echo "✓ SeaweedFS integration tests passed."
        echo ""

        echo "Running SeaweedFS Topology & Failover Tests..."
        "$SCRIPT_DIR/run-seaweedfs-topology-tests.sh"
        echo "✓ SeaweedFS topology tests passed."
        echo ""
    else
        echo "⚠️ Skipping SeaweedFS tests because docker command is not available."
    fi
fi

# Step 4: Frontend Web Unit Tests
if [ "$RUN_WEB" -eq 1 ] && [ -d "$WEB_DIR" ]; then
    echo "------------------------------------------"
    echo "[4/4] Running Frontend Web Unit Tests..."
    echo "------------------------------------------"
    if command -v pnpm >/dev/null 2>&1; then
        cd "$WEB_DIR"
        pnpm test
        echo "✓ Web unit tests passed."
        echo ""
    else
        echo "⚠️ Skipping web tests because pnpm command is not available."
    fi
fi

echo "=========================================="
echo " 🎉 All requested test suites passed!"
echo "=========================================="
