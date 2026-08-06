from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_production_master_restore_guards_numeric_volume_id_monotonicity() -> None:
    compose = _read("compose.yaml")
    master = compose.split("  seaweedfs-master:", 1)[1].split("  seaweedfs-volume1:", 1)[0]
    assert "-mdir=/data" in master
    assert "-defaultReplication=010" in master
    assert "/opt/seaweedfs/master:/data" in master

    topology_test = _read("api/scripts/run-seaweedfs-topology-tests.sh")
    assert "MASTER_BACKUP=" in topology_test
    assert 'docker rm -f "$MASTER"' in topology_test
    assert 'rm -rf "$MASTER_DATA"' in topology_test
    assert 'cp -a "$MASTER_BACKUP/." "$MASTER_DATA/"' in topology_test
    assert "fid_volume_id()" in topology_test
    assert "master_max_volume_id()" in topology_test
    assert "delayed_volume_id=" in topology_test
    assert "dataNode=${VOLUME2}:8082" in topology_test
    assert '"$stale_max" -lt "$delayed_volume_id"' in topology_test
    assert '"$post_grow_max" -gt "$delayed_volume_id"' in topology_test
    assert '"$new_volume_id" -gt "$delayed_volume_id"' in topology_test
    assert "new_fid = initial_fid" not in topology_test


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

    for variable in (
        "API_IMAGE",
        "WORKER_IMAGE",
        "WEB_IMAGE",
        "POSTGRES_IMAGE",
        "REDIS_IMAGE",
        "NGINX_IMAGE",
        "MEILI_IMAGE",
        "EUROOFFICE_IMAGE",
    ):
        assert f"image: ${{{variable}" in production
    assert "image: ${SEAWEEDFS_IMAGE" in _read("compose.yaml")
    assert production.count("build: !reset null") >= 6
    assert "command: null" in production
    assert "command: []" not in production
    assert production.count("image: docker.io/library/alpine@${POLICY_IMAGE_DIGEST:") == 4
    assert "check POLICY_IMAGE_DIGEST" in production

    release_env = _read("deploy/production-images.env.example")
    for variable in (
        "POSTGRES_IMAGE",
        "REDIS_IMAGE",
        "NGINX_IMAGE",
        "MEILI_IMAGE",
        "EUROOFFICE_IMAGE",
        "SEAWEEDFS_IMAGE",
    ):
        assert f"{variable}=" in release_env


def test_every_manifest_platform_is_scanned_before_promotion() -> None:
    build = _read(".github/workflows/build.yml")
    scanner = _read(".github/workflows/scan-and-promote.yml")

    assert "\n  workflow_call:" in build.split("jobs:", 1)[0]
    assert "\n  push:" not in build.split("jobs:", 1)[0]
    assert build.count("uses: ./.github/workflows/scan-and-promote.yml") == 4
    assert "linux/amd64,linux/arm64" in build
    assert "api-candidate@${{ needs.build-api.outputs.api_digest }}" in build
    assert "worker-candidate@${{ needs.build-api.outputs.worker_digest }}" in build

    assert "fail-fast: false" in scanner
    assert "platform: linux/amd64" in scanner
    assert "platform: linux/arm64" in scanner
    assert "TRIVY_PLATFORM: ${{ matrix.platform }}" in scanner
    assert "trivy-${{ inputs.component }}-${{ matrix.slug }}.sarif" in scanner
    assert "needs: [validate, scan]" in scanner
    assert scanner.index("  scan:") < scanner.index("  promote:")
    assert "exit-code: '1'" in scanner


def test_premerge_ci_is_separate_from_main_and_tag_release() -> None:
    ci = _read(".github/workflows/ci.yml")
    release = _read(".github/workflows/release.yml")
    build = _read(".github/workflows/build.yml")

    ci_trigger = ci.split("concurrency:", 1)[0]
    assert "workflow_call:" in ci_trigger
    assert "pull_request:" in ci_trigger
    assert "push:" in ci_trigger
    assert "uv run ruff check ." in ci
    assert "uv run mypy app/" in ci
    assert 'uv run pytest -m "not integration"' in ci
    assert "uv run alembic upgrade head" in ci
    assert "tests/integration/database/test_revert_concurrency.py" in ci
    assert "pnpm test" in ci
    assert "npm run typecheck:node" in ci
    assert "needs: [api, postgres-revert, production-policy, web, delivery]" in ci
    assert "for result in" in ci

    release_trigger = release.split("jobs:", 1)[0]
    assert "branches: [main]" in release_trigger
    assert "tags: ['alpha-*']" in release_trigger
    assert "uses: ./.github/workflows/ci.yml" in release
    assert "needs: ci" in release
    assert "uses: ./.github/workflows/build.yml" in release
    assert "\n  push:" not in build.split("jobs:", 1)[0]


def test_revert_database_invariant_is_migrated() -> None:
    model = _read("api/app/models/pull_request.py")
    migration = _read("api/app/migrations/versions/unique_reverts_pr_id.py")
    assert "uq_pull_requests_reverts_pr_id" in model
    assert "with_for_update()" in _read("api/app/services/pr.py")
    assert "create_unique_constraint" in migration
    assert "HAVING COUNT(*) > 1" in migration
