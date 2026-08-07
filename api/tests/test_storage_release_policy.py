"""Static release-policy regressions for storage-dependent images and runbooks."""

from pathlib import Path

_REPO_ROOT = Path(__file__).parents[2]


def test_api_image_publication_depends_on_both_live_seaweedfs_jobs() -> None:
    workflow = (_REPO_ROOT / ".github/workflows/build.yml").read_text()
    assert "workflow_call:" in workflow.split("jobs:", 1)[0]
    assert "test-seaweedfs:" in workflow
    assert "test-seaweedfs-topology:" in workflow
    assert "needs: [test-seaweedfs, test-seaweedfs-topology]" in workflow
    assert (
        workflow.count("SEAWEEDFS_IMAGE: ${{ needs.resolve-seaweedfs-image.outputs.image }}") == 2
    )
    build_section = workflow.split("  build-api:", 1)[1].split("\n  build-web:", 1)[0]
    assert build_section.count("push: true") == 2


def test_standalone_seaweedfs_workflow_is_premerge_not_independent_publish_gate() -> None:
    workflow = (_REPO_ROOT / ".github/workflows/seaweedfs-integration.yml").read_text()
    trigger_block = workflow.split("permissions:", 1)[0]
    assert "pull_request:" in trigger_block
    assert "workflow_dispatch:" in trigger_block
    assert "  push:" not in trigger_block
    assert "resolve-seaweedfs-image:" in workflow
    assert (
        workflow.count("SEAWEEDFS_IMAGE: ${{ needs.resolve-seaweedfs-image.outputs.image }}") == 2
    )


def test_migration_runbook_always_uses_production_override_and_digest() -> None:
    runbook = (_REPO_ROOT / "docs/r2-to-seaweedfs-migration.md").read_text()
    assert "@sha256:<tested-manifest-digest>" in runbook
    assert "-f compose.yaml -f compose.prod.yaml" in runbook
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
