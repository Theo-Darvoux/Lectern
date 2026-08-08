from __future__ import annotations

import re
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
    assert 'git diff --quiet -- .' in prepare
    assert 'git diff --cached --quiet -- .' in prepare
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
