from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_production_compose_is_accepted_by_the_real_compose_cli(tmp_path: Path) -> None:
    docker = shutil.which("docker")
    if docker is None:
        return

    runtime_env = tmp_path / "runtime.env"
    runtime_env.write_text("ENVIRONMENT=production\n", encoding="utf-8")
    env = {
        **os.environ,
        "RUNTIME_ENV_FILE": str(runtime_env),
        "EUROOFFICE_JWT_SECRET": "compose-policy-test",
        "WORKER_ZIP_HMAC_SECRET": "compose-hmac-test-secret",
        "S3_ACCESS_KEY": "compose-access-key",
        "S3_SECRET_KEY": "compose-secret-key",
    }
    command = [
        docker,
        "compose",
        "-f",
        "compose.yaml",
        "-f",
        "compose.prod.yaml",
        "--profile",
        "postgres",
        "--profile",
        "selfhost-worker",
        "--profile",
        "seaweedfs-prod",
        "config",
    ]
    subprocess.run(
        [*command, "--quiet"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    rendered = subprocess.run(
        [*command, "--format", "json"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    config = json.loads(rendered.stdout)
    delivery = config["services"]["selfhost-worker"]
    assert delivery["environment"]["S3_ENDPOINT"] == "seaweedfs-s3:8333"
    assert delivery["environment"]["WORKER_ZIP_HMAC_SECRET"] == "compose-hmac-test-secret"
    assert delivery["read_only"] is True
    assert delivery["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in delivery["security_opt"]
    assert delivery["ports"] == [
        {
            "mode": "ingress",
            "target": 8788,
            "published": "8788",
            "protocol": "tcp",
            "host_ip": "127.0.0.1",
        }
    ]


def test_production_master_restore_guards_numeric_volume_id_monotonicity() -> None:
    compose = _read("compose.yaml")
    master = compose.split("  seaweedfs-master:", 1)[1].split("  seaweedfs-volume1:", 1)[0]
    assert "-mdir=/data" in master
    assert "-defaultReplication=010" in master
    assert "/opt/seaweedfs/master:/data" in master

    topology_test = _read("api/scripts/run-seaweedfs-topology-tests.sh")
    assert "MASTER_BACKUP_VOLUME=" in topology_test
    assert 'docker rm -f "$MASTER"' in topology_test
    assert "copy_volume_contents" in topology_test
    assert 'copy_volume_contents "$MASTER_BACKUP_VOLUME" "$MASTER_DATA_VOLUME"' in topology_test
    assert "fid_volume_id()" in topology_test
    assert "master_max_volume_id()" in topology_test
    assert "delayed_volume_id=" in topology_test
    assert "dataNode=${VOLUME2}:8082" in topology_test
    assert '"$stale_max" -lt "$delayed_volume_id"' in topology_test
    assert '"$post_grow_max" -gt "$delayed_volume_id"' in topology_test
    assert '"$new_volume_id" -gt "$delayed_volume_id"' in topology_test


def test_production_overlay_forces_hardened_runtime_and_compose_trusts_its_proxy() -> None:
    production = _read("compose.prod.yaml")
    for service, next_service in (
        ("api", "worker"),
        ("worker", "worker-fast"),
        ("worker-fast", "worker-slow"),
        ("worker-slow", "web"),
    ):
        block = production.split(f"  {service}:", 1)[1].split(f"\n  {next_service}:", 1)[0]
        assert "ENVIRONMENT: production" in block

    compose = _read("compose.yaml")
    trusted_default = (
        "TRUSTED_PROXY_HOSTS: ${TRUSTED_PROXY_HOSTS:-"
        "127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16}"
    )
    assert trusted_default in compose


def test_parser_hosts_drop_default_capabilities_and_forbid_privilege_escalation() -> None:
    base = _read("compose.yaml")
    worker_base = base.split("x-worker-base: &worker-base", 1)[1].split("x-worker-watch:", 1)[0]
    assert "- no-new-privileges:true" in worker_base
    assert "cap_drop:\n    - ALL" in worker_base
    assert "- SYS_ADMIN" not in worker_base
    for capability in ("SETUID", "SETGID", "SETFCAP"):
        assert f"- {capability}" in worker_base

    api = base.split("  api:", 1)[1].split("\n  worker:", 1)[0]
    assert "- no-new-privileges:true" in api
    assert "cap_drop:\n      - ALL" in api
    assert "- SYS_ADMIN" not in api
    for capability in ("SETUID", "SETGID", "SETFCAP"):
        assert f"- {capability}" in api


def test_authenticated_delivery_never_uses_pre_auth_nginx_cache() -> None:
    worker_cache = _read("infra/nginx/worker-cache.conf")
    file_location = worker_cache.split("location /file/ {", 1)[1].split("\n}", 1)[0]
    assert "proxy_cache off;" in file_location
    assert "proxy_cache worker_cache;" not in file_location
    assert 'proxy_cache_key "$uri"' not in file_location


def test_self_hosted_delivery_container_drops_root() -> None:
    dockerfile = _read("worker/Dockerfile")
    assert "USER node" in dockerfile
    assert "npm ci --omit=dev" in dockerfile
    assert "COPY --from=build /app/dist ./dist" in dockerfile
    assert "./node_modules/.bin/tsx" not in dockerfile.split("FROM node:", 2)[-1]
    assert dockerfile.index("USER node") < dockerfile.index('CMD ["node", "dist/node/server.js"')


def test_self_hosted_delivery_uses_hardened_runtime_and_production_storage_endpoint() -> None:
    compose = _read("compose.yaml")
    block = compose.split("  selfhost-worker:", 1)[1].split("\nnetworks:", 1)[0]
    assert "127.0.0.1:${SELFHOST_WORKER_HOST_PORT:-8788}:8788" in block
    assert "read_only: true" in block
    assert "- no-new-privileges:true" in block
    assert "cap_drop:\n      - ALL" in block
    assert "pids: 128" in block
    assert "WORKER_ZIP_HMAC_SECRET must be set" in block

    production = _read("compose.prod.yaml")
    production_block = production.split("  selfhost-worker:", 1)[1].split("\n  nginx:", 1)[0]
    assert "S3_ENDPOINT: ${SELFHOST_WORKER_S3_ENDPOINT:-seaweedfs-s3:8333}" in production_block
    assert "S3_ACCESS_KEY must be set" in production_block
    assert "S3_SECRET_KEY must be set" in production_block

    server = _read("worker/src/node/server.ts")
    assert 'throw new Error("WORKER_ZIP_HMAC_SECRET must contain at least 32 bytes")' in server


def test_postgresql_transaction_pooling_is_rejected_for_cas_session_fencing() -> None:
    with pytest.raises(ValueError, match="DATABASE_POOL_MODE=transaction"):
        Settings(
            _env_file=None,
            database_url="postgresql+asyncpg://lectern:lectern@postgres:5432/lectern",
            database_pool_mode="transaction",
        )

    env_example = _read(".env.example")
    assert "DATABASE_POOL_MODE=session" in env_example
    assert "Transaction pooling is intentionally rejected" in env_example


def test_sandbox_launcher_error_is_not_misreported_as_child_exit() -> None:
    from app.core.security.sandbox import (
        SandboxInfrastructureError,
        _raise_if_sandbox_launcher_failed,
    )

    with pytest.raises(SandboxInfrastructureError, match="bwrap"):
        _raise_if_sandbox_launcher_failed(1, b"bwrap: setting up uid map: Permission denied\n")


def test_base_compose_does_not_advertise_profile_only_production_startup() -> None:
    compose = _read("compose.yaml")
    header = "\n".join(compose.splitlines()[:30])
    assert "-f compose.yaml -f compose.prod.yaml" in header
    assert "127.0.0.1:${API_HOST_PORT:-8000}:8000" in compose


def test_topology_backup_restore_is_independent_of_host_uid() -> None:
    script = _read("api/scripts/run-seaweedfs-topology-tests.sh")

    assert 'MASTER_DATA_VOLUME="${PREFIX}-master-data"' in script
    assert 'MASTER_BACKUP_VOLUME="${PREFIX}-master-backup"' in script
    assert 'docker volume create "$MASTER_DATA_VOLUME"' in script
    assert 'docker volume create "$MASTER_BACKUP_VOLUME"' in script
    assert 'copy_volume_contents "$MASTER_DATA_VOLUME" "$MASTER_BACKUP_VOLUME"' in script
    assert 'copy_volume_contents "$MASTER_BACKUP_VOLUME" "$MASTER_DATA_VOLUME"' in script
    assert "--user 0:0" in script
    assert "--entrypoint /bin/sh" in script
    assert '-v "$source_volume:/source:ro"' in script
    assert '-v "$destination_volume:/destination"' in script
    assert "cp -a /source/. /destination/" in script
    assert 'docker volume rm "$MASTER_BACKUP_VOLUME" "$MASTER_DATA_VOLUME"' in script

    forbidden_host_state_operations = (
        'cp -a "$MASTER_DATA/."',
        'cp -a "$MASTER_BACKUP/."',
        'rm -rf "$MASTER_DATA"',
        'find "$MASTER_DATA"',
    )
    for forbidden in forbidden_host_state_operations:
        assert forbidden not in script


def test_production_topology_storage_proof_is_independent_of_redis() -> None:
    conftest = _read("api/tests/integration/storage/conftest.py")
    topology_runner = _read("api/scripts/run-seaweedfs-topology-tests.sh")
    topology_test = _read("api/tests/integration/storage/test_zz_seaweedfs_topology_failover.py")

    # The topology runner identifies itself explicitly but does not provision
    # Redis. Its proof is SeaweedFS replication/failover, not CAS accounting.
    assert "SEAWEEDFS_TOPOLOGY=production" in topology_runner
    assert "REDIS_URL=" not in topology_runner

    # The shared fixture bypasses Redis only for that explicit shard.
    assert 'os.environ.get("SEAWEEDFS_TOPOLOGY") == "production"' in conftest
    assert "yield None" in conftest
    assert "REDIS_URL must be set by the SeaweedFS storage-semantics runner" in conftest

    # Keep the topology proof on ordinary non-CAS keys. If this changes to CAS,
    # the shard must deliberately gain Redis rather than silently inheriting it.
    assert 'prefix = f"integration/{uuid.uuid4().hex}"' in conftest
    assert 'storage_key("cross-rack-failover.bin")' in topology_test
    assert '"cas/' not in topology_test
