from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_production_master_is_recreated_from_persistent_state() -> None:
    compose = _read("compose.yaml")
    master = compose.split("  seaweedfs-master:", 1)[1].split("  seaweedfs-volume1:", 1)[0]
    assert "-mdir=/data" in master
    assert "-defaultReplication=010" in master
    assert "/opt/seaweedfs/master:/data" in master
    topology_test = _read("api/scripts/run-seaweedfs-topology-tests.sh")
    assert 'docker rm -f "$MASTER"' in topology_test
    assert 'docker stop "$VOLUME2"' in topology_test
    assert 'docker start "$VOLUME2"' in topology_test
    assert 'find "$MASTER_DATA" -mindepth 1' in topology_test
    assert "new_fid = initial_fid" not in topology_test


def test_production_overlay_accepts_only_post_scan_release_repositories() -> None:
    production = _read("compose.prod.yaml")
    assert "api-release@sha256:[0-9a-f]{64}" in production
    assert "worker-release@sha256:[0-9a-f]{64}" in production
    assert "web-release@sha256:[0-9a-f]{64}" in production
    assert "selfhost-worker-release@sha256:[0-9a-f]{64}" in production
    assert "image: ${API_IMAGE}" in production
    assert production.count("image: ${WORKER_IMAGE}") == 3
    assert "image: ${WEB_IMAGE}" in production
    assert "image: ${SELFHOST_WORKER_IMAGE" in production
    assert production.count("build: !reset null") >= 6
    assert "command: null" in production
    assert "command: []" not in production
    env_example = _read(".env.example")
    assert "api-release@sha256:" in env_example
    assert "SELFHOST_WORKER_IMAGE=" in env_example


def test_build_uses_candidate_repositories_and_post_scan_copy() -> None:
    workflow = _read(".github/workflows/build.yml")
    assert "api-candidate" in workflow
    assert "worker-candidate" in workflow
    assert "web-candidate" in workflow
    assert "selfhost-worker-candidate" in workflow
    assert "api-release" in workflow
    assert "selfhost-worker-release" in workflow
    assert workflow.count("exit-code: '1'") == 4
    assert workflow.count("./scripts/promote-release-image.sh") == 4
    assert workflow.count("outputs.digest") >= 8
    assert workflow.count("CANDIDATE_IMAGE }}@${{ steps.") == 4
    assert "docker buildx imagetools create \\" not in workflow
    assert workflow.index("Scan api candidate (Trivy)") < workflow.index(
        "Promote api and worker release images"
    )
    assert workflow.index("Scan delivery candidate (Trivy)") < workflow.index(
        "Promote delivery release image"
    )


def test_production_policy_job_cannot_be_skipped_by_path_filter() -> None:
    workflow = _read(".github/workflows/build.yml")
    block = workflow.split("  test-production-policy:", 1)[1].split("\n  test-", 1)[0]
    assert "\n    if:" not in block
    assert "test_production_hardening_policy.py" in block
    assert "test_release_promotion_script.py" in block
    assert "policy:" in workflow
    assert "compose.prod.yaml" in workflow
    assert "scripts/promote-release-image.sh" in workflow


def test_revert_database_invariant_is_migrated() -> None:
    model = _read("api/app/models/pull_request.py")
    migration = _read("api/app/migrations/versions/unique_reverts_pr_id.py")
    assert "uq_pull_requests_reverts_pr_id" in model
    assert "with_for_update()" in _read("api/app/services/pr.py")
    assert "create_unique_constraint" in migration
    assert "HAVING COUNT(*) > 1" in migration
