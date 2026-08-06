# CI and release gates

`.github/workflows/ci.yml` runs on pull requests and non-main branch pushes. It is also reusable: `.github/workflows/release.yml` invokes the same workflow on `main` and `alpha-*` tags before the release implementation is allowed to build candidates.

Configure the repository ruleset for `main` to require the stable **CI / required** check. That aggregation job fails unless the API lint/type/unit suite, real-PostgreSQL migration and revert-concurrency suite, production-policy suite, web suite, and delivery-worker suite all succeed.

The existing SeaweedFS pull-request workflow remains the live storage gate for its path set. Release publication additionally reruns both live SeaweedFS suites inside `build.yml` before API and worker candidates are built.

Only `release.yml` has a main/tag push trigger. `build.yml` and `scan-and-promote.yml` are reusable implementation workflows and cannot publish from a direct repository event. Promotion is downstream of both the amd64 and arm64 Trivy matrix entries.
