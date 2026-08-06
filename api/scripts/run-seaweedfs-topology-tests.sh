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
MASTER_BACKUP="${TMPDIR:-/tmp}/${PREFIX}-master-backup"

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
    rm -rf "$MASTER_DATA" "$MASTER_BACKUP"
    exit "$status"
}
trap cleanup EXIT INT TERM

for command in docker curl uv python3; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "$command is required" >&2
        exit 1
    }
done

start_master() {
    docker run -d --name "$MASTER" --network "$NETWORK" \
        -p "127.0.0.1:${MASTER_PORT}:9333" \
        -v "$MASTER_DATA:/data:Z" \
        "$SEAWEEDFS_IMAGE" \
        master -ip="$MASTER" -ip.bind=0.0.0.0 -port=9333 -mdir=/data \
        -defaultReplication=010 -volumeSizeLimitMB=1024 >/dev/null
}

wait_for_master() {
    attempt=0
    until curl --silent --fail "http://127.0.0.1:${MASTER_PORT}/cluster/healthz" >/dev/null 2>&1; do
        attempt=$((attempt + 1))
        [ "$attempt" -lt 60 ] || {
            echo "SeaweedFS master did not become healthy" >&2
            exit 1
        }
        sleep 1
    done
}

master_max_volume_id() {
    curl --silent --show-error --fail "http://127.0.0.1:${MASTER_PORT}/vol/status" \
        | python3 -c '
import json
import sys

payload = json.load(sys.stdin)
ids = []

def visit(value):
    if isinstance(value, dict):
        raw_id = value.get("Id")
        if isinstance(raw_id, int):
            ids.append(raw_id)
        elif isinstance(raw_id, str) and raw_id.isdigit():
            ids.append(int(raw_id))
        for nested in value.values():
            visit(nested)
    elif isinstance(value, list):
        for nested in value:
            visit(nested)

visit(payload.get("Volumes", {}))
print(max(ids, default=0))
'
}

wait_for_max_at_least() {
    expected=$1
    description=$2
    attempt=0
    while :; do
        observed=$(master_max_volume_id 2>/dev/null || printf '0')
        if [ "$observed" -ge "$expected" ]; then
            printf '%s\n' "$observed"
            return 0
        fi
        attempt=$((attempt + 1))
        [ "$attempt" -lt 60 ] || {
            echo "SeaweedFS master did not observe $description volume ID $expected" >&2
            exit 1
        }
        sleep 1
    done
}

json_fid() {
    python3 -c 'import json,sys; print(json.load(sys.stdin).get("fid", ""))'
}

json_count() {
    python3 -c 'import json,sys; print(json.load(sys.stdin).get("count", 0))'
}

fid_volume_id() {
    fid=$1
    volume_id=${fid%%,*}
    case "$volume_id" in
        ''|*[!0-9]*)
            echo "invalid SeaweedFS fid: $fid" >&2
            exit 1
            ;;
    esac
    printf '%s\n' "$volume_id"
}

assign_fid() {
    replication=$1
    collection=$2
    attempt=0
    fid=""
    while [ -z "$fid" ]; do
        assignment=$(curl --silent --fail --get \
            --data-urlencode "replication=$replication" \
            --data-urlencode "collection=$collection" \
            "http://127.0.0.1:${MASTER_PORT}/dir/assign" 2>/dev/null || true)
        if [ -n "$assignment" ]; then
            fid=$(printf '%s' "$assignment" | json_fid 2>/dev/null || true)
        fi
        attempt=$((attempt + 1))
        [ -n "$fid" ] || [ "$attempt" -lt 60 ] || {
            echo "SeaweedFS could not allocate replication=$replication for $collection" >&2
            exit 1
        }
        [ -n "$fid" ] || sleep 1
    done
    printf '%s\n' "$fid"
}

docker rm -f "$S3" "$FILER" "$VOLUME2" "$VOLUME1" "$MASTER" >/dev/null 2>&1 || true
docker network rm "$NETWORK" >/dev/null 2>&1 || true
rm -rf "$MASTER_DATA" "$MASTER_BACKUP"
mkdir -p "$MASTER_DATA" "$MASTER_BACKUP"
docker network create "$NETWORK" >/dev/null

start_master
wait_for_master

docker run -d --name "$VOLUME1" --network "$NETWORK" \
    "$SEAWEEDFS_IMAGE" \
    volume -dir=/data -ip="$VOLUME1" -ip.bind=0.0.0.0 \
    -mserver="$MASTER:9333" -port=8081 -dataCenter=dc1 -rack=rack1 >/dev/null

docker run -d --name "$VOLUME2" --network "$NETWORK" \
    "$SEAWEEDFS_IMAGE" \
    volume -dir=/data -ip="$VOLUME2" -ip.bind=0.0.0.0 \
    -mserver="$MASTER:9333" -port=8082 -dataCenter=dc1 -rack=rack2 >/dev/null

# Establish a valid cross-rack allocation and record the numeric volume ID.
initial_fid=$(assign_fid 010 initial-topology-guard)
initial_volume_id=$(fid_volume_id "$initial_fid")
initial_max=$(wait_for_max_at_least "$initial_volume_id" initial)

if ! find "$MASTER_DATA" -mindepth 1 -print -quit | grep -q .; then
    echo "SeaweedFS master did not persist state under -mdir=/data" >&2
    exit 1
fi

# Take a coherent backup of the master before creating the highest numbered
# volume. This backup deliberately models restoring an older production backup.
docker stop "$MASTER" >/dev/null
cp -a "$MASTER_DATA/." "$MASTER_BACKUP/"
docker start "$MASTER" >/dev/null
wait_for_master
wait_for_max_at_least "$initial_max" restored-in-place >/dev/null

# Create one unreplicated volume specifically on rack2. It becomes the highest
# known volume ID, but is absent from the older master backup.
grow_response=$(curl --silent --show-error --fail --get \
    --data-urlencode 'count=1' \
    --data-urlencode 'replication=000' \
    --data-urlencode 'collection=late-id-guard' \
    --data-urlencode 'dataCenter=dc1' \
    --data-urlencode 'rack=rack2' \
    --data-urlencode "dataNode=${VOLUME2}:8082" \
    "http://127.0.0.1:${MASTER_PORT}/vol/grow")
[ "$(printf '%s' "$grow_response" | json_count)" -eq 1 ] || {
    echo "SeaweedFS did not create the delayed highest-ID volume on rack2" >&2
    exit 1
}
delayed_volume_id=$(master_max_volume_id)
[ "$delayed_volume_id" -gt "$initial_max" ] || {
    echo "SeaweedFS did not advance the maximum volume ID" >&2
    exit 1
}

# Restore the older master backup while the server carrying the highest ID is
# offline. The stale master must fail closed until that server reconnects.
docker stop "$VOLUME2" >/dev/null
docker rm -f "$MASTER" >/dev/null
rm -rf "$MASTER_DATA"
mkdir -p "$MASTER_DATA"
cp -a "$MASTER_BACKUP/." "$MASTER_DATA/"
start_master
wait_for_master
sleep 3

stale_max=$(master_max_volume_id)
[ "$stale_max" -lt "$delayed_volume_id" ] || {
    echo "restored master unexpectedly retained delayed volume ID $delayed_volume_id" >&2
    exit 1
}

for _attempt in 1 2 3 4 5; do
    partial=$(curl --silent --fail --get \
        --data-urlencode 'replication=010' \
        --data-urlencode 'collection=partial-recovery-guard' \
        "http://127.0.0.1:${MASTER_PORT}/dir/assign" 2>/dev/null || true)
    partial_fid=""
    if [ -n "$partial" ]; then
        partial_fid=$(printf '%s' "$partial" | json_fid 2>/dev/null || true)
    fi
    if [ -n "$partial_fid" ]; then
        echo "SeaweedFS allocated replication=010 while rack2 was offline" >&2
        exit 1
    fi
    sleep 1
done

# The late server must raise the master's observed maximum before new volumes
# are created. A subsequent cross-rack grow must use a strictly larger ID.
docker start "$VOLUME2" >/dev/null
wait_for_max_at_least "$delayed_volume_id" delayed >/dev/null

grow_response=$(curl --silent --show-error --fail --get \
    --data-urlencode 'count=1' \
    --data-urlencode 'replication=010' \
    --data-urlencode 'collection=post-restore-id-guard' \
    "http://127.0.0.1:${MASTER_PORT}/vol/grow")
[ "$(printf '%s' "$grow_response" | json_count)" -eq 1 ] || {
    echo "SeaweedFS could not grow a new cross-rack volume after rack2 rejoined" >&2
    exit 1
}
post_grow_max=$(master_max_volume_id)
[ "$post_grow_max" -gt "$delayed_volume_id" ] || {
    echo "SeaweedFS reused or regressed the maximum volume ID after backup restore" >&2
    exit 1
}

new_fid=$(assign_fid 010 post-restore-id-guard)
new_volume_id=$(fid_volume_id "$new_fid")
[ "$new_volume_id" -gt "$delayed_volume_id" ] || {
    echo "SeaweedFS assigned volume ID $new_volume_id after delayed maximum $delayed_volume_id" >&2
    exit 1
}

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
