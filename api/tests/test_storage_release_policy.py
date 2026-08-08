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
    assert 'cp -a /source/. /destination/' in script
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
