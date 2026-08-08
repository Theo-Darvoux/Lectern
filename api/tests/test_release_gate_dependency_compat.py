from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (_REPO_ROOT / path).read_text(encoding="utf-8")


def _job(text: str, name: str) -> str:
    match = re.search(rf"(?m)^  {re.escape(name)}:\n", text)
    assert match is not None
    next_job = re.search(r"(?m)^  [A-Za-z0-9_-]+:\n", text[match.end() :])
    end = len(text) if next_job is None else match.end() + next_job.start()
    return text[match.start() : end]


def test_release_requires_reusable_ci_before_publish_implementation() -> None:
    release = _read(".github/workflows/release.yml")
    release_job = _job(release, "release")
    assert "needs: ci" in release_job
    assert "uses: ./.github/workflows/build.yml" in release_job
    assert "packages: write" in release_job
    assert "group: production-release" in release
    assert "cancel-in-progress: false" in release


def test_release_implementation_has_only_the_ci_gated_caller() -> None:
    callers = []
    for path in sorted((_REPO_ROOT / ".github/workflows").glob("*.yml")):
        source = path.read_text(encoding="utf-8")
        if "uses: ./.github/workflows/build.yml" in source:
            callers.append(path.name)
    assert callers == ["release.yml"]


def test_release_reuses_exact_seaweedfs_image_exercised_by_required_ci() -> None:
    ci = _read(".github/workflows/ci.yml")
    release = _read(".github/workflows/release.yml")
    build = _read(".github/workflows/build.yml")

    seaweed = _job(ci, "seaweedfs")
    assert "Live SeaweedFS S3/storage semantics" in seaweed
    assert "Production-topology persistence, replication and failover" in seaweed
    assert "tested_seaweedfs_image:" in ci
    assert "value: ${{ jobs.production-policy.outputs.seaweedfs_image }}" in ci

    release_job = _job(release, "release")
    assert "tested_seaweedfs_image: ${{ needs.ci.outputs.tested_seaweedfs_image }}" in release_job

    validator = _job(build, "validate-tested-storage")
    assert "inputs.tested_seaweedfs_image" in validator
    assert "steps.toolchain.outputs.seaweedfs_test_image" in validator
    assert '[[ "$TESTED_IMAGE" == "$PINNED_IMAGE" ]]' in validator

    assert "test-seaweedfs:" not in build
    assert "test-seaweedfs-topology:" not in build
    assert "resolve-seaweedfs-image:" not in build


def test_promotion_is_downstream_of_complete_platform_matrix() -> None:
    scanner = _read(".github/workflows/scan-and-promote.yml")
    promotion = _job(scanner, "promote")
    assert "needs: [validate, scan, runtime-smoke]" in promotion
    assert "runtime-smoke:" in scanner
    assert "TRIVY_PLATFORM: ${{ matrix.platform }}" in scanner
    assert "platform: linux/amd64" in scanner
    assert "platform: linux/arm64" in scanner
    assert "release_digest:" in scanner
    assert "alias_name" not in scanner


def test_manifest_is_aggregate_release_gate_and_aliases_are_not_automated() -> None:
    build = _read(".github/workflows/build.yml")
    finalizer = _job(build, "finalize-release")
    for dependency in (
        "validate-tested-storage",
        "release-api",
        "release-worker",
        "release-web",
        "release-delivery",
    ):
        assert f"- {dependency}" in finalizer
    assert "production-release-${{ github.sha }}" in finalizer
    assert "inspect-production-images.py" in finalizer
    assert "validate-production-compose.py" in finalizer
    assert "production-compose-services.json" in finalizer
    assert "--compose-service-map-file" in finalizer
    assert "publish-aliases:" not in build
    assert "publish-release-aliases.sh" not in build


def test_postgres_and_storage_gates_run_before_merge() -> None:
    ci = _read(".github/workflows/ci.yml")
    postgres = _job(ci, "postgres-revert")
    seaweed = _job(ci, "seaweedfs")
    required = _job(ci, "required")

    assert "uv run alembic upgrade head" in postgres
    assert "tests/integration/database/test_revert_concurrency.py" in postgres
    assert "pull_request:" in ci.split("jobs:", 1)[0]

    assert "run-seaweedfs-integration-tests.sh" in seaweed
    assert "run-seaweedfs-topology-tests.sh" in seaweed
    assert "- seaweedfs" in required
    assert "SEAWEEDFS_RESULT" in required
    assert "SEAWEEDFS_TOPOLOGY_RESULT" not in required
