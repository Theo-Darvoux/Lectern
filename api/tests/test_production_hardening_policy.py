from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_production_compose_is_accepted_by_the_real_compose_cli(tmp_path: Path) -> None:
    docker = shutil.which("docker")
    if docker is None:
        return

    runtime_env = tmp_path / "runtime.env"
    runtime_env.write_text("ENVIRONMENT=production\n", encoding="utf-8")
    digest = "a" * 64
    env = {
        **os.environ,
        "RUNTIME_ENV_FILE": str(runtime_env),
        "EUROOFFICE_JWT_SECRET": "compose-policy-test",
        "API_IMAGE": f"ghcr.io/theo-darvoux/lectern/api-release@sha256:{digest}",
        "WORKER_IMAGE": f"ghcr.io/theo-darvoux/lectern/worker-release@sha256:{digest}",
        "WEB_IMAGE": f"ghcr.io/theo-darvoux/lectern/web-release@sha256:{digest}",
        "SELFHOST_WORKER_IMAGE": (
            f"ghcr.io/theo-darvoux/lectern/selfhost-worker-release@sha256:{digest}"
        ),
        "REDIS_IMAGE": f"docker.io/library/redis@sha256:{digest}",
        "NGINX_IMAGE": f"docker.io/library/nginx@sha256:{digest}",
        "MEILI_IMAGE": f"docker.io/getmeili/meilisearch@sha256:{digest}",
        "EUROOFFICE_IMAGE": f"ghcr.io/euro-office/documentserver@sha256:{digest}",
        "POLICY_IMAGE_DIGEST": f"sha256:{digest}",
        "POSTGRES_IMAGE": f"docker.io/library/postgres@sha256:{digest}",
        "SEAWEEDFS_IMAGE": f"docker.io/chrislusf/seaweedfs@sha256:{digest}",
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


def test_production_overlay_pins_workloads_infrastructure_and_policy_helper() -> None:
    production = _read("compose.prod.yaml")
    expected_repositories = (
        "api-release@sha256:[0-9a-f]{64}",
        "worker-release@sha256:[0-9a-f]{64}",
        "web-release@sha256:[0-9a-f]{64}",
        "selfhost-worker-release@sha256:[0-9a-f]{64}",
        "docker\\.io/library/postgres@sha256:[0-9a-f]{64}",
        "docker\\.io/library/redis@sha256:[0-9a-f]{64}",
        "docker\\.io/library/nginx@sha256:[0-9a-f]{64}",
        "docker\\.io/getmeili/meilisearch@sha256:[0-9a-f]{64}",
        "ghcr\\.io/euro-office/documentserver@sha256:[0-9a-f]{64}",
        "docker\\.io/chrislusf/seaweedfs@sha256:[0-9a-f]{64}",
    )
    for repository in expected_repositories:
        assert repository in production
    assert production.count("image: docker.io/library/alpine@${POLICY_IMAGE_DIGEST:") == 4


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


def test_every_manifest_platform_is_scanned_before_immutable_promotion() -> None:
    build = _read(".github/workflows/build.yml")
    scanner = _read(".github/workflows/scan-and-promote.yml")
    assert "linux/amd64,linux/arm64" in build
    assert build.count("uses: ./.github/workflows/scan-and-promote.yml") == 4
    assert "fail-fast: false" in scanner
    assert "platform: linux/amd64" in scanner
    assert "platform: linux/arm64" in scanner
    assert "TRIVY_PLATFORM: ${{ matrix.platform }}" in scanner
    assert "needs: [validate, scan]" in scanner
    assert "alias_name" not in scanner


def test_release_completion_is_manifest_driven_without_automated_cross_repo_aliases() -> None:
    build = _read(".github/workflows/build.yml")
    assert "  finalize-release:" in build
    assert "Publish authoritative release-complete artifact" in build
    assert "production-release-${{ github.sha }}" in build
    assert "validate-production-compose.py" in build
    assert "production-compose-services.json" in build
    assert "release-toolchain.env" in build
    assert "--compose-service-map-file" in build
    assert "production-compose.config.yml" not in build
    assert "production-compose-images.txt" not in build
    assert "  publish-aliases:" not in build
    assert "publish-release-aliases.sh" not in build
    promote = _read("scripts/promote-release-image.sh")
    assert "Commit tags are write-once" in promote
    assert "immutable release tag already exists with a different digest" in promote


def test_release_manifest_input_is_strict_canonical_and_secret_safe() -> None:
    library = _read("scripts/release_manifest_lib.py")
    prepare = _read("scripts/prepare-production-release.sh")
    compose = _read("compose.yaml")
    assert "unsupported release variable" in library
    assert "shell-style export assignments are forbidden" in library
    assert "duplicate variable" in library
    assert 'key != "COMPOSE_PROFILES"' in library
    assert 'if raw == ""' in library
    assert "--canonical-manifest" in prepare
    assert "materialize-production-deployment.py" in prepare
    assert "--runtime-env" in prepare
    assert "config --quiet --no-env-resolution" in prepare
    assert "--format json --no-env-resolution" in prepare
    assert "production-compose.config.yml" not in prepare
    assert "production-compose-images.txt" not in prepare
    assert "git diff --quiet -- ." in prepare
    assert "git diff --cached --quiet -- ." in prepare
    assert "validate-production-compose.py" in prepare
    assert "inspect-production-images.py" in prepare
    assert compose.count("env_file: ${RUNTIME_ENV_FILE:-.env}") == 5


def test_premerge_ci_installs_real_sandbox_runtime_and_requires_storage() -> None:
    ci = _read(".github/workflows/ci.yml")
    assert "pull_request:" in ci.split("jobs:", 1)[0]
    assert "sudo apt-get install --yes --no-install-recommends bubblewrap" in ci
    assert "kernel.apparmor_restrict_unprivileged_userns" in ci
    assert "kernel.apparmor_restrict_unprivileged_unconfined" in ci
    assert "Smoke-test real sandbox runtime" in ci
    assert 'uv run pytest -m "not integration"' in ci
    assert "  seaweedfs:" in ci
    assert "  seaweedfs-production-topology:" not in ci
    seaweed = ci.split("  seaweedfs:", 1)[1].split("\n  web:", 1)[0]
    assert "suite: storage-semantics" in seaweed
    assert "suite: production-topology" in seaweed
    assert "run-seaweedfs-integration-tests.sh" in seaweed
    assert "run-seaweedfs-topology-tests.sh" in seaweed
    required = ci.split("  required:", 1)[1]
    assert "- seaweedfs" in required
    assert "- seaweedfs-production-topology" not in required


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

    ci = _read(".github/workflows/ci.yml")
    delivery = ci.split("  delivery:", 1)[1].split("\n  required:", 1)[0]
    assert "npm test" in delivery
    assert "npm run test:node" in delivery


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


def test_all_external_actions_are_pinned_to_full_commit_shas() -> None:
    workflow_dir = REPO_ROOT / ".github/workflows"
    unpinned: list[str] = []
    for path in sorted(workflow_dir.glob("*.yml")):
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = re.search(r"\buses:\s*([^\s#]+)", line)
            if match is None:
                continue
            reference = match.group(1)
            if reference.startswith("./"):
                continue
            if re.fullmatch(r"[^@]+@[0-9a-f]{40}", reference) is None:
                unpinned.append(f"{path.name}:{line_number}:{reference}")
    assert not unpinned, unpinned
    dependabot = _read(".github/dependabot.yml")
    assert "package-ecosystem: github-actions" in dependabot
