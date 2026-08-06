#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
API_DIR="$REPO_ROOT/api"

SEAWEEDFS_IMAGE=${SEAWEEDFS_IMAGE:-chrislusf/seaweedfs:4.29}
S3_PORT=${SEAWEEDFS_TOPOLOGY_S3_PORT:-28333}
MASTER_PORT=${SEAWEEDFS_TOPOLOGY_MASTER_PORT:-29333}
FILER_PORT=${SEAWEEDFS_TOPOLOGY_FILER_PORT:-28888}
PREFIX=${SEAWEEDFS_TOPOLOGY_PREFIX:-lectern-seaweedfs-topology}
NETWORK="${PREFIX}-network"
MASTER="${PREFIX}-master"
VOLUME1="${PREFIX}-volume1"
VOLUME2="${PREFIX}-volume2"
FILER="${PREFIX}-filer"
S3="${PREFIX}-s3"
MASTER_DATA="${TMPDIR:-/tmp}/${PREFIX}-master-data"

cleanup() {
    status=$?
    trap - EXIT INT TERM
    if [ "$status" -ne 0 ]; then
        for container in "$MASTER" "$VOLUME1" "$VOLUME2" "$FILER" "$S3"; do
            echo "--- $container logs ---" >&2
            docker logs "$container" 2>/dev/null || true
        done
    fi
    docker rm -f "$S3" "$FILER" "$VOLUME2" "$VOLUME1" "$MASTER" >/dev/null 2>&1 || true
    docker network rm "$NETWORK" >/dev/null 2>&1 || true
    rm -rf "$MASTER_DATA"
    exit "$status"
}
trap cleanup EXIT INT TERM

for command in docker curl uv; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "$command is required" >&2
        exit 1
    }
done

docker rm -f "$S3" "$FILER" "$VOLUME2" "$VOLUME1" "$MASTER" >/dev/null 2>&1 || true
docker network rm "$NETWORK" >/dev/null 2>&1 || true
rm -rf "$MASTER_DATA"
mkdir -p "$MASTER_DATA"
docker network create "$NETWORK" >/dev/null

docker run -d --name "$MASTER" --network "$NETWORK" \
    -p "127.0.0.1:${MASTER_PORT}:9333" \
    -v "$MASTER_DATA:/data:Z" \
    "$SEAWEEDFS_IMAGE" \
    master -ip="$MASTER" -ip.bind=0.0.0.0 -port=9333 -mdir=/data \
    -defaultReplication=010 -volumeSizeLimitMB=1024 >/dev/null

attempt=0
until curl --silent --fail "http://127.0.0.1:${MASTER_PORT}/cluster/healthz" >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    [ "$attempt" -lt 60 ] || {
        echo "SeaweedFS master did not become healthy" >&2
        exit 1
    }
    sleep 1
done

docker run -d --name "$VOLUME1" --network "$NETWORK" \
    "$SEAWEEDFS_IMAGE" \
    volume -dir=/data -ip="$VOLUME1" -ip.bind=0.0.0.0 \
    -mserver="$MASTER:9333" -port=8081 -dataCenter=dc1 -rack=rack1 >/dev/null

docker run -d --name "$VOLUME2" --network "$NETWORK" \
    "$SEAWEEDFS_IMAGE" \
    volume -dir=/data -ip="$VOLUME2" -ip.bind=0.0.0.0 \
    -mserver="$MASTER:9333" -port=8082 -dataCenter=dc1 -rack=rack2 >/dev/null

# Do not start the filer/S3 gateway until the master can allocate a volume with
# the production rack-aware policy. This catches an invalid topology before the
# application tests begin.
attempt=0
initial_fid=""
while [ -z "$initial_fid" ]; do
    assignment=$(curl --silent --fail \
        "http://127.0.0.1:${MASTER_PORT}/dir/assign?replication=010" 2>/dev/null || true)
    initial_fid=$(printf '%s' "$assignment" | sed -n 's/.*"fid":"\([^"]*\)".*/\1/p')
    attempt=$((attempt + 1))
    [ -n "$initial_fid" ] || [ "$attempt" -lt 60 ] || {
        echo "SeaweedFS could not allocate replication=010 across both racks" >&2
        exit 1
    }
    [ -n "$initial_fid" ] || sleep 1
done

if ! find "$MASTER_DATA" -mindepth 1 -print -quit | grep -q .; then
    echo "SeaweedFS master did not persist state under -mdir=/data" >&2
    exit 1
fi

# Recreate, rather than restart, the master. Only the mounted -mdir survives.
# Keep rack2 offline so replication=010 must fail closed during partial recovery.
docker stop "$VOLUME2" >/dev/null
docker rm -f "$MASTER" >/dev/null
docker run -d --name "$MASTER" --network "$NETWORK" \
    -p "127.0.0.1:${MASTER_PORT}:9333" \
    -v "$MASTER_DATA:/data:Z" \
    "$SEAWEEDFS_IMAGE" \
    master -ip="$MASTER" -ip.bind=0.0.0.0 -port=9333 -mdir=/data \
    -defaultReplication=010 -volumeSizeLimitMB=1024 >/dev/null

attempt=0
until curl --silent --fail "http://127.0.0.1:${MASTER_PORT}/cluster/healthz" >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    [ "$attempt" -lt 60 ] || {
        echo "SeaweedFS master did not recover from persistent state" >&2
        exit 1
    }
    sleep 1
done

# Give rack1 time to reconnect, then prove the missing rack is not silently ignored.
sleep 3
for _attempt in 1 2 3 4 5; do
    partial=$(curl --silent --fail \
        "http://127.0.0.1:${MASTER_PORT}/dir/assign?replication=010" 2>/dev/null || true)
    if printf '%s' "$partial" | grep -q '"fid"'; then
        echo "SeaweedFS allocated replication=010 while rack2 was offline" >&2
        exit 1
    fi
    sleep 1
done

docker start "$VOLUME2" >/dev/null
attempt=0
new_fid=""
while [ -z "$new_fid" ]; do
    assignment=$(curl --silent --fail \
        "http://127.0.0.1:${MASTER_PORT}/dir/assign?replication=010" 2>/dev/null || true)
    new_fid=$(printf '%s' "$assignment" | sed -n 's/.*"fid":"\([^"]*\)".*/\1/p')
    attempt=$((attempt + 1))
    [ -n "$new_fid" ] || [ "$attempt" -lt 60 ] || {
        echo "SeaweedFS could not allocate after rack2 rejoined" >&2
        exit 1
    }
    [ -n "$new_fid" ] || sleep 1
done

if [ "$new_fid" = "$initial_fid" ]; then
    echo "SeaweedFS reused file identifier $new_fid after master recreation" >&2
    exit 1
fi

docker run -d --name "$FILER" --network "$NETWORK" \
    -p "127.0.0.1:${FILER_PORT}:8888" \
    "$SEAWEEDFS_IMAGE" \
    filer -ip="$FILER" -ip.bind=0.0.0.0 -port=8888 \
    -master="$MASTER:9333" -defaultReplicaPlacement=010 -defaultStoreDir=/data >/dev/null

attempt=0
until curl --silent --fail "http://127.0.0.1:${FILER_PORT}/" >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    [ "$attempt" -lt 60 ] || {
        echo "SeaweedFS filer did not become healthy" >&2
        exit 1
    }
    sleep 1
done

docker run -d --name "$S3" --network "$NETWORK" \
    -p "127.0.0.1:${S3_PORT}:8333" \
    -v "$REPO_ROOT/infra/docker/seaweedfs/s3.json:/etc/seaweedfs/s3.json:ro,Z" \
    "$SEAWEEDFS_IMAGE" \
    s3 -ip.bind=0.0.0.0 -port=8333 -filer="$FILER:8888" \
    -config=/etc/seaweedfs/s3.json >/dev/null

cd "$API_DIR"
SEAWEEDFS_INTEGRATION=1 \
SEAWEEDFS_TOPOLOGY=production \
SEAWEEDFS_TOPOLOGY_VOLUME1="$VOLUME1" \
SEAWEEDFS_TOPOLOGY_VOLUME2="$VOLUME2" \
SEAWEEDFS_TEST_ENDPOINT="127.0.0.1:${S3_PORT}" \
SEAWEEDFS_TEST_ACCESS_KEY=minioadmin \
SEAWEEDFS_TEST_SECRET_KEY=minioadmin \
uv run pytest -m integration \
    tests/integration/storage/test_zz_seaweedfs_topology_failover.py \
    -ra "$@"
