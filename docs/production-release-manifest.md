# Production release manifest

The canonical `production-release-<commit>` GitHub Actions artifact is the authoritative **release complete** marker. Component tags and convenience aliases are not deployable release identities.

## Repository-pinned control plane

`deploy/release-toolchain.env` contains reviewed, repository-controlled release inputs:

- an exact Docker Buildx version;
- a digest-pinned BuildKit image;
- a digest-pinned `tonistiigi/binfmt` image used only for ARM64 emulation;
- the reviewed SeaweedFS release metadata and exact digest exercised by required CI and release tests.

Updating any of these values is an explicit reviewed source change. GitHub Action source references remain pinned to full commit SHAs as a separate supply-chain control.

## Automated release sequence

`release.yml` reruns the complete CI workflow, then `build.yml` performs these phases:

1. Load and validate the repository-pinned release toolchain.
2. Run both live SeaweedFS suites against the exact `SEAWEEDFS_TEST_IMAGE` digest committed in `deploy/release-toolchain.env`.
3. Build all four candidate images for `linux/amd64` and `linux/arm64` using the pinned Buildx, BuildKit, and binfmt inputs.
4. Scan both child platforms for every candidate.
5. Copy only write-once `sha-<commit>` tags into release repositories. Same-digest reruns are idempotent; conflicting reuse fails closed.
6. Wait for API, worker, web, and self-hosted worker promotion to succeed.
7. Require the independently reviewed SeaweedFS expectation to equal the repository-tested digest and use the tested digest in release input.
8. Verify every workload commit tag, infrastructure digest, and required platform set through the registry.
9. Render Compose with only checked-in synthetic runtime values and certify the exact **service→image** mapping. The validator rejects missing, extra, or swapped service images.
10. Write a deterministic schema-v3 manifest containing Compose hashes, registry evidence, service→image evidence, and exact release-toolchain provenance.
11. Publish the canonical manifest, sanitized image file, registry inspection, minimized Compose service map, pinned `release-toolchain.env`, and `SHA256SUMS` as one authoritative artifact.

A failed workflow that copied one or more immutable component tags but did not publish the canonical artifact is incomplete and must not be deployed. Releases are serialized repository-wide and an in-progress release is never cancelled by a newer release.

Cross-repository convenience aliases are deliberately not part of automated release completion. `scripts/publish-release-aliases.sh` is a manual, best-effort navigation helper only and must never be used as a production deployment input.

## Secret handling

Release artifacts must never contain production runtime secrets. The automated finalizer uses `.env.example` only for structural Compose rendering and persists only the minimized service→image evidence.

Local preparation requires an explicit runtime environment file, but uses it only for non-outputting `docker compose config --quiet --no-env-resolution` validation. It never persists a Compose model rendered from production secrets.

`compose.yaml` service `env_file` references are redirected through `RUNTIME_ENV_FILE`, so release tooling never has to copy a production `.env` into the repository checkout.

## Local deployment preparation

Local preparation does **not** accept a user-authored image trust file. Start from the exact release commit and extract the canonical manifest from the corresponding `production-release-<commit>` artifact. Then run:

```bash
./scripts/prepare-production-release.sh \
  --canonical-manifest /secure/release/production-<commit>.json \
  --runtime-env /secure/runtime/production.env
```

To deploy only a certified subset of optional profiles:

```bash
./scripts/prepare-production-release.sh \
  --canonical-manifest /secure/release/production-<commit>.json \
  --runtime-env /secure/runtime/production.env \
  --profiles seaweedfs-prod,selfhost-worker
```

The selected profiles must be a subset of those certified by the canonical release. Image references are always derived from the canonical manifest; operators cannot substitute another workload, infrastructure, or SeaweedFS digest.

The command:

- rejects modified or staged tracked files;
- validates the canonical manifest against the checked-out commit, Compose hashes, embedded evidence, and pinned toolchain;
- derives a sanitized deployment image file from the canonical manifest;
- re-verifies registry availability, workload commit-tag binding, and platforms;
- validates real runtime interpolation without writing rendered secret-bearing configuration;
- renders a synthetic Compose JSON model and verifies the exact service→image mapping;
- writes only the canonical manifest copy, derived image selection, registry inspection, minimized service map, selection metadata, and checksums.

`deploy/production-images.env.example` remains documentation for the generated schema, not a release-authoring template.

## Repository controls

The `main` ruleset should require pull requests and the stable `required` GitHub Actions check, block force pushes and deletions, and restrict bypasses. Repository Actions settings should require full-length action SHA references. Dependabot proposes controlled GitHub Actions updates.
