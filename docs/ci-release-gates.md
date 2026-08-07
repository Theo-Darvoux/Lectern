# CI and release gates

`.github/workflows/ci.yml` runs on pull requests and non-main branch pushes. `release.yml` invokes the same reusable workflow on `main` and `alpha-*` tags before any candidate image can be built.

The stable **CI / required** aggregation job fails unless all of the following succeed:

- API Ruff, mypy, and hermetic tests, with Bubblewrap installed for real sandbox tests;
- every database migration and the real PostgreSQL revert-concurrency invariant;
- release and deployment policy regressions;
- web lint, type, i18n, and tests;
- delivery-worker tests and type checks.

The release implementation additionally runs both live SeaweedFS suites, builds all four multi-platform components, scans AMD64 and ARM64 separately, copies immutable commit tags, verifies registry provenance, publishes a checksummed canonical release artifact, and only then updates convenience aliases.

Every external action is pinned to a reviewed full commit SHA. `.github/dependabot.yml` proposes controlled updates instead of allowing mutable major-version tags to move underneath privileged workflows.

Administrative controls remain required: configure the `main` ruleset to require **CI / required**, and enable the repository Actions setting that requires full-length action SHA references.
