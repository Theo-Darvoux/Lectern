from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_production_master_uses_persistent_state_directory() -> None:
    compose = _read("compose.yaml")
    master = compose.split("  seaweedfs-master:", 1)[1].split("  seaweedfs-volume1:", 1)[0]
    assert "-mdir=/data" in master
    assert "-defaultReplication=010" in master
    assert "/opt/seaweedfs/master:/data" in master
    topology_test = _read("api/scripts/run-seaweedfs-topology-tests.sh")
    assert 'docker restart "$MASTER"' in topology_test
    assert 'find "$MASTER_DATA" -mindepth 1' in topology_test


def test_production_overlay_requires_immutable_workload_digests() -> None:
    production = _read("compose.prod.yaml")
    assert 'case "$$value" in *@sha256:*' in production
    assert "image: ${API_IMAGE}" in production
    assert production.count("image: ${WORKER_IMAGE}") == 3
    assert "image: ${WEB_IMAGE}" in production
    env_example = _read(".env.example")
    assert "#API_IMAGE=ghcr.io/theo-darvoux/lectern/api@sha256:" in env_example


def test_build_only_promotes_mutable_aliases_after_blocking_scans() -> None:
    workflow = _read(".github/workflows/build.yml")
    assert "type=raw,value=latest" not in workflow
    assert "type=ref,event=tag" not in workflow
    assert workflow.count("exit-code: '1'") == 3
    assert workflow.index("Scan api image (Trivy)") < workflow.index(
        "Promote api and worker release aliases"
    )
    assert workflow.index("Scan worker image (Trivy)") < workflow.index(
        "Promote api and worker release aliases"
    )
    assert workflow.index("Scan web image (Trivy)") < workflow.index(
        "Promote web release alias"
    )
