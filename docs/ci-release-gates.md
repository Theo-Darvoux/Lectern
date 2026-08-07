# CI and release gates

`.github/workflows/ci.yml` runs on pull requests and non-main branch pushes. `release.yml` invokes the same reusable workflow on `main` and `alpha-*` tags before any candidate image can be built.

The stable **CI / required** aggregation job fails unless all of the following succeed:

- API Ruff, mypy, and hermetic tests, with Bubblewrap installed and a real sandbox smoke test;
- every database migration and the real PostgreSQL revert-concurrency invariant;
- release and deployment policy regressions;
- both live SeaweedFS suites, using a registry-resolved immutable digest;
- web lint, type, i18n, and tests;
- delivery-worker tests and type checks.

The release implementation additionally reruns both live SeaweedFS suites before API/worker publication, builds all four multi-platform components, scans AMD64 and ARM64 separately, copies write-once `sha-<commit>` tags, verifies registry provenance, verifies the production Compose image set, and publishes a checksummed canonical release artifact.

Convenience aliases are **not** updated automatically. Cross-repository aliases cannot be transactionally atomic, so the canonical `production-release-<commit>` artifact and digest-pinned Compose references are the only deployable release identity. `scripts/publish-release-aliases.sh` remains available for optional manual, best-effort aliases only.

Every external action is pinned to a reviewed full commit SHA. `.github/dependabot.yml` proposes controlled updates instead of allowing mutable major-version tags to move underneath privileged workflows.

Administrative controls remain required: configure the `main` ruleset to require **CI / required**, require pull requests, block force pushes and deletions, tightly restrict bypasses, and enable the repository Actions setting that requires full-length action SHA references.
