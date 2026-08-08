# CI and release gates

`.github/workflows/ci.yml` runs on pull requests and non-main branch pushes. `release.yml` invokes the same reusable workflow on `main` and `alpha-*` tags before candidate publication.

The stable **CI / required** aggregation fails unless all of the following succeed:

- API Ruff, mypy, and hermetic tests, including a real Bubblewrap smoke test;
- every database migration and the PostgreSQL revert-concurrency invariant;
- release and deployment policy regressions;
- both live SeaweedFS suites using the exact repository-pinned digest in `deploy/release-toolchain.env`;
- web lint, type, i18n, and tests;
- delivery-worker tests and type checks.

Required CI no longer resolves a mutable SeaweedFS tag. `SEAWEEDFS_VERSION` is human-readable reviewed metadata; `SEAWEEDFS_TEST_IMAGE` is the immutable execution input. A SeaweedFS upgrade therefore requires a reviewed repository change.

The release path additionally uses a repository-pinned Buildx version, digest-pinned BuildKit image, and digest-pinned binfmt image. The exact control-plane inputs are embedded in the canonical release manifest.

Release finalization verifies registry provenance and certifies the exact production Compose **service→image** mapping, not only the image set. Swapped API/worker references, missing services, and unexpected services fail certification.

Release artifacts never include a Compose model rendered from production secrets. Automated certification uses synthetic checked-in runtime values; local runtime secrets are used only by a non-outputting validation command. The persisted Compose evidence is a minimized service→digest map.

Local deployment preparation consumes the canonical release manifest and can only choose a certified optional-profile subset. It cannot author replacement image digests.

Convenience aliases are not updated automatically. The canonical `production-release-<commit>` artifact and digest-pinned Compose references are the only deployable release identity.

Every external action is pinned to a reviewed full commit SHA. `.github/dependabot.yml` proposes controlled updates instead of allowing mutable action tags to move under privileged workflows.
