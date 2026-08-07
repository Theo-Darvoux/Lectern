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
    assert "cancel-in-progress: false" in release


def test_build_api_preserves_both_live_storage_release_gates() -> None:
    build = _read(".github/workflows/build.yml")
    build_api = _job(build, "build-api")
    assert "needs: [test-seaweedfs, test-seaweedfs-topology]" in build_api
    assert "test-seaweedfs:" in build
    assert "test-seaweedfs-topology:" in build


def test_promotion_is_downstream_of_complete_platform_matrix() -> None:
    scanner = _read(".github/workflows/scan-and-promote.yml")
    promotion = _job(scanner, "promote")
    assert "needs: [validate, scan]" in promotion
    assert "TRIVY_PLATFORM: ${{ matrix.platform }}" in scanner
    assert "platform: linux/amd64" in scanner
    assert "platform: linux/arm64" in scanner
    assert "release_digest:" in scanner
    assert "alias_name" not in scanner


def test_manifest_and_aliases_are_aggregate_release_gates() -> None:
    build = _read(".github/workflows/build.yml")
    finalizer = _job(build, "finalize-release")
    aliases = _job(build, "publish-aliases")
    all_components = "[release-api, release-worker, release-web, release-delivery]"
    assert f"needs: {all_components}" in finalizer
    assert "production-release-${{ github.sha }}" in finalizer
    assert "inspect-production-images.py" in finalizer
    assert "needs: [release-api, release-worker, release-web, release-delivery, finalize-release]" in aliases
    assert "publish-release-aliases.sh" in aliases
    for name in ("release-api", "release-worker", "release-web", "release-delivery"):
        assert "alias_name:" not in _job(build, name)


def test_postgres_revert_gate_runs_before_merge_and_release() -> None:
    ci = _read(".github/workflows/ci.yml")
    postgres = _job(ci, "postgres-revert")
    assert "uv run alembic upgrade head" in postgres
    assert "tests/integration/database/test_revert_concurrency.py" in postgres
    assert "pull_request:" in ci.split("jobs:", 1)[0]
