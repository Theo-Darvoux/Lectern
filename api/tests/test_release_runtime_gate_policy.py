from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

DEBIAN_SNAPSHOT = "20260801T000000Z"
POSTGRES_DIGEST = "57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777"

EXPECTED = {
    "python": "57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de",
    "node": "16e22a550f3863206a3f701448c45f7912c6896a62de43add43bb9c86130c3e2",
    "nginx": "54f2a904c251d5a34adf545a72d32515a15e08418dae0266e23be2e18c66fefa",
    "uv": "2b56d2665e5591ce8a1e527b1471637fba03e46324236e4a1bc7c591f56a4edf",
}


def _external_image_references(path: Path) -> list[str]:
    stage_names: set[str] = set()
    references: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.upper().startswith("FROM "):
            tokens = line.split()
            index = 1
            while index < len(tokens) and tokens[index].startswith("--"):
                index += 1
            image = tokens[index]
            if image not in stage_names:
                references.append(image)
            if "AS" in [token.upper() for token in tokens]:
                for pos, token in enumerate(tokens[:-1]):
                    if token.upper() == "AS":
                        stage_names.add(tokens[pos + 1])
                        break
        if line.startswith("COPY --from="):
            image = line.split("--from=", 1)[1].split()[0]
            if image not in stage_names:
                references.append(image)
    return references


def test_production_docker_build_roots_are_digest_pinned() -> None:
    dockerfiles = [
        REPO_ROOT / "api/Dockerfile",
        REPO_ROOT / "web/Dockerfile",
        REPO_ROOT / "worker/Dockerfile",
    ]
    digest_re = re.compile(r"@sha256:[0-9a-f]{64}$")
    for dockerfile in dockerfiles:
        refs = _external_image_references(dockerfile)
        assert refs, dockerfile
        for reference in refs:
            assert digest_re.search(reference), (dockerfile, reference)

    api = (REPO_ROOT / "api/Dockerfile").read_text(encoding="utf-8")
    web = (REPO_ROOT / "web/Dockerfile").read_text(encoding="utf-8")
    worker = (REPO_ROOT / "worker/Dockerfile").read_text(encoding="utf-8")
    assert f"python:3.12-slim-trixie@sha256:{EXPECTED['python']}" in api
    assert f"ghcr.io/astral-sh/uv:0.12.3-python3.13-dhi@sha256:{EXPECTED['uv']}" in api
    assert f"node:22-alpine@sha256:{EXPECTED['node']}" in web
    assert f"nginx:alpine@sha256:{EXPECTED['nginx']}" in web
    assert f"node:22-alpine@sha256:{EXPECTED['node']}" in worker
    assert "apk upgrade" not in web
    assert "corepack prepare" not in web
    assert "RUN corepack enable" in web
    package_json = (REPO_ROOT / "web/package.json").read_text(encoding="utf-8")
    assert "pnpm@10.32.1+sha512." in package_json
    assert "npx" not in worker
    assert "./node_modules/.bin/tsx" in worker
    assert "# syntax=docker/dockerfile:1" not in api
    assert f"ARG DEBIAN_SNAPSHOT={DEBIAN_SNAPSHOT}" in api
    assert api.count("snapshot.debian.org/archive/debian/") >= 2
    assert "deb.debian.org" not in api


def test_release_promotion_requires_exact_candidate_runtime_execution() -> None:
    workflow = (REPO_ROOT / ".github/workflows/scan-and-promote.yml").read_text(
        encoding="utf-8"
    )
    smoke = (REPO_ROOT / "scripts/smoke-release-candidate.sh").read_text(
        encoding="utf-8"
    )

    assert "runtime-smoke:" in workflow
    assert "linux/amd64" in workflow
    assert "linux/arm64" in workflow
    assert 'needs: [validate, scan, runtime-smoke]' in workflow
    assert './scripts/smoke-release-candidate.sh "$COMPONENT" "$CANDIDATE_REF" "$PLATFORM"' in workflow
    assert "steps.toolchain.outputs.binfmt_image" in workflow
    assert f"postgres:16-alpine@sha256:{POSTGRES_DIGEST}" in workflow

    assert '@sha256:' in smoke
    assert 'docker buildx imagetools inspect --raw' in smoke
    assert 'child_digest=' in smoke
    assert 'candidate_ref="${candidate_repository}@${child_digest}"' in smoke
    assert '--platform "$platform"' in smoke
    assert 'RUN_MIGRATIONS=false' in smoke
    assert '--network host' in smoke
    assert 'uvicorn app.main:app' in smoke
    assert '/api/health' in smoke
    assert 'docker exec "$name" /venv/bin/python' in smoke
    assert '--entrypoint /venv/bin/python' not in smoke
    for component in ("api", "worker", "web", "selfhost-worker"):
        assert component in smoke


def test_required_ci_includes_real_redis_auth_atomicity() -> None:
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "redis-auth-atomicity:" in workflow
    assert "AUTH_ATOMICITY_REDIS_URL" in workflow
    assert "tests/integration/test_auth_redis_atomicity.py" in workflow
    assert "REDIS_AUTH_RESULT" in workflow
    assert "redis:7.4-alpine@sha256:e7723ff73d963f5cc6d9c4643ea3d989527a402a319239054e9472a7fb9219a2" in workflow
    assert f"postgres:16-alpine@sha256:{POSTGRES_DIGEST}" in workflow
