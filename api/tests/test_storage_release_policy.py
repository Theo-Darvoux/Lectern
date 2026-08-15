"""Static release-policy regressions for storage-dependent images and runbooks."""

from pathlib import Path

_REPO_ROOT = Path(__file__).parents[2]


def test_api_image_publication_depends_on_ci_tested_seaweedfs_identity() -> None:
    release = (_REPO_ROOT / ".github/workflows/release.yml").read_text()
    build = (_REPO_ROOT / ".github/workflows/build.yml").read_text()

    assert "workflow_call:" in build.split("jobs:", 1)[0]
    assert "tested_seaweedfs_image:" in build.split("jobs:", 1)[0]
    assert "tested_seaweedfs_image: ${{ needs.ci.outputs.tested_seaweedfs_image }}" in release
    assert "  validate-tested-storage:" in build
    validator = build.split("  validate-tested-storage:", 1)[1].split("\n  build-api:", 1)[0]
    assert "inputs.tested_seaweedfs_image" in validator
    assert "steps.toolchain.outputs.seaweedfs_test_image" in validator
    assert '[[ "$TESTED_IMAGE" == "$PINNED_IMAGE" ]]' in validator

    build_section = build.split("  build-api:", 1)[1].split("\n  build-web:", 1)[0]
    assert "needs: validate-tested-storage" in build_section
    assert build_section.count("push: true") == 2


def test_standalone_seaweedfs_workflow_is_manual_candidate_diagnostic() -> None:
    workflow = (_REPO_ROOT / ".github/workflows/seaweedfs-integration.yml").read_text()
    trigger_block = workflow.split("permissions:", 1)[0]
    assert "pull_request:" not in trigger_block
    assert "workflow_dispatch:" in trigger_block
    assert "  push:" not in trigger_block
    assert "resolve-seaweedfs-image:" in workflow
    assert "./scripts/resolve-seaweedfs-image.sh" in workflow
    assert "RepoDigests" not in workflow
    assert "docker image inspect" not in workflow
    tested_image_line = "SEAWEEDFS_IMAGE: ${{ needs.resolve-seaweedfs-image.outputs.image }}"
    assert sum(line.strip() == tested_image_line for line in workflow.splitlines()) == 2


def test_live_storage_invariants_are_parallel_matrix_shards_under_one_required_job_id() -> None:
    ci = (_REPO_ROOT / ".github/workflows/ci.yml").read_text()
    assert "  resolve-seaweedfs-image:" not in ci
    assert "  seaweedfs:" in ci
    assert "  seaweedfs-production-topology:" not in ci

    seaweed = ci.split("  seaweedfs:", 1)[1].split("\n  web:", 1)[0]
    assert "strategy:" in seaweed
    assert "fail-fast: false" in seaweed
    assert "suite: storage-semantics" in seaweed
    assert "suite: production-topology" in seaweed
    assert "run-seaweedfs-integration-tests.sh" in seaweed
    assert "run-seaweedfs-topology-tests.sh" in seaweed
    assert "steps.toolchain.outputs.seaweedfs_test_image" in seaweed

    required = ci.split("  required:", 1)[1]
    assert "- seaweedfs" in required
    assert "- seaweedfs-production-topology" not in required
    assert '"$SEAWEEDFS_RESULT"' in required
    assert '"$SEAWEEDFS_TOPOLOGY_RESULT"' not in required


def test_topology_backup_restore_is_independent_of_host_uid() -> None:
    script = (_REPO_ROOT / "api/scripts/run-seaweedfs-topology-tests.sh").read_text()

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


def test_migration_runbook_requires_the_canonical_production_release() -> None:
    runbook = (_REPO_ROOT / "docs/r2-to-seaweedfs-migration.md").read_text()
    assert "production-<commit>.deployment-images.env" in runbook
    assert "Do not export a hand-written `SEAWEEDFS_IMAGE`" in runbook
    assert "profile from `compose.yaml` alone" in runbook
    assert "replication=010" in runbook
    assert "replication=001" not in runbook

    for line in runbook.splitlines():
        if "seaweedfs-prod" in line and "docker compose" in line:
            assert "compose.prod.yaml" in line or line.rstrip().endswith("\\")


def test_base_compose_does_not_advertise_profile_only_production_startup() -> None:
    compose = (_REPO_ROOT / "compose.yaml").read_text()
    header = "\n".join(compose.splitlines()[:30])
    assert "-f compose.yaml -f compose.prod.yaml" in header
    assert "127.0.0.1:${API_HOST_PORT:-8000}:8000" in compose


def test_required_ci_redis_identity_is_bound_to_production_release() -> None:
    toolchain_lines = (_REPO_ROOT / "deploy/release-toolchain.env").read_text().splitlines()
    toolchain = dict(
        line.split("=", 1) for line in toolchain_lines if line and not line.startswith("#")
    )
    redis_version = toolchain["REDIS_VERSION"]
    redis_image = toolchain["REDIS_TEST_IMAGE"]
    assert redis_version == "7.4"
    assert redis_image.startswith("docker.io/library/redis@sha256:")
    redis_digest = redis_image.rsplit("@sha256:", 1)[1]
    redis_ci_ref = f"docker.io/library/redis:{redis_version}-alpine@sha256:{redis_digest}"

    ci = (_REPO_ROOT / ".github/workflows/ci.yml").read_text()
    release = (_REPO_ROOT / ".github/workflows/release.yml").read_text()
    build = (_REPO_ROOT / ".github/workflows/build.yml").read_text()

    assert ci.count(f"image: {redis_ci_ref}") == 3
    assert "tested_redis_image:" in ci.split("jobs:", 1)[0]
    postgres_revert = ci.split("  postgres-revert:", 1)[1].split("\n  production-policy:", 1)[0]
    assert f"image: {redis_ci_ref}" in postgres_revert
    assert "AUTH_ATOMICITY_REDIS_URL: redis://127.0.0.1:6379/15" in postgres_revert
    assert "test_cas_storage_process_fence.py" in postgres_revert
    assert "redis_image: ${{ steps.toolchain.outputs.redis_test_image }}" in ci
    assert "tested_redis_image: ${{ needs.ci.outputs.tested_redis_image }}" in release

    header = build.split("jobs:", 1)[0]
    assert "tested_redis_image:" in header
    validator = build.split("  validate-tested-storage:", 1)[1].split("\n  build-api:", 1)[0]
    assert "inputs.tested_redis_image" in validator
    assert "steps.toolchain.outputs.redis_test_image" in validator
    assert '[[ "$TESTED_REDIS_IMAGE" == "$PINNED_REDIS_IMAGE" ]]' in validator

    finalize = build.split("  finalize-release:", 1)[1]
    assert "require-tested-redis-image.sh" in finalize
    assert "REDIS_IMAGE=${TESTED_REDIS_IMAGE}" in finalize


def test_tested_redis_guard_rejects_release_digest_drift() -> None:
    import subprocess

    script = _REPO_ROOT / "scripts/require-tested-redis-image.sh"
    tested = "docker.io/library/redis@sha256:" + "a" * 64
    approved = "docker.io/library/redis@sha256:" + "b" * 64
    result = subprocess.run(
        [str(script), tested, approved],
        cwd=_REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 65
    assert "does not match" in result.stderr


def test_production_topology_storage_proof_is_independent_of_redis() -> None:
    conftest_path = _REPO_ROOT / "api/tests/integration/storage/conftest.py"
    topology_runner_path = _REPO_ROOT / "api/scripts/run-seaweedfs-topology-tests.sh"
    topology_test_path = (
        _REPO_ROOT / "api/tests/integration/storage/test_zz_seaweedfs_topology_failover.py"
    )

    assert conftest_path.is_file(), conftest_path
    assert topology_runner_path.is_file(), topology_runner_path
    assert topology_test_path.is_file(), topology_test_path

    conftest = conftest_path.read_text(encoding="utf-8")
    topology_runner = topology_runner_path.read_text(encoding="utf-8")
    topology_test = topology_test_path.read_text(encoding="utf-8")

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
