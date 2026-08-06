from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOW = _REPO_ROOT / ".github/workflows/build.yml"


def _job(text: str, name: str) -> str:
    match = re.search(rf"(?m)^  {re.escape(name)}:\n", text)
    assert match is not None
    next_job = re.search(r"(?m)^  [A-Za-z0-9_-]+:\n", text[match.end() :])
    end = len(text) if next_job is None else match.end() + next_job.start()
    return text[match.start() : end]


def test_build_api_preserves_storage_release_dependency_contract() -> None:
    workflow = _WORKFLOW.read_text(encoding="utf-8")
    build_api = _job(workflow, "build-api")
    assert "needs: [changes, test-api, test-seaweedfs, test-seaweedfs-topology]" in build_api


def test_api_tests_transitively_require_policy_and_postgres_revert_gates() -> None:
    workflow = _WORKFLOW.read_text(encoding="utf-8")
    test_api = _job(workflow, "test-api")
    assert "needs: [changes, test-production-policy, test-revert-postgres]" in test_api
