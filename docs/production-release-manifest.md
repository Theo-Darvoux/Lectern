# Production release manifest

The canonical `production-release-<commit>` GitHub Actions artifact is the authoritative **release complete** marker. Component tags and convenience aliases do not identify a complete release.

## Automated release sequence

`release.yml` reruns the complete CI workflow, then `build.yml` performs these phases:

1. Resolve `chrislusf/seaweedfs:4.29` directly from registry manifest metadata to a canonical `docker.io/chrislusf/seaweedfs@sha256:...` reference.
2. Run both live SeaweedFS suites against that exact digest.
3. Build all four candidate images for `linux/amd64` and `linux/arm64`.
4. Scan both child platforms for every candidate.
5. Copy only write-once `sha-<commit>` tags into the release repositories. A same-digest rerun is idempotent; a different digest for an existing commit tag fails closed.
6. Wait for API, worker, web, and self-hosted worker promotion to succeed.
7. Require the protected `PRODUCTION_SEAWEEDFS_IMAGE` expectation to equal the digest exercised by the live suites, and place the tested digest—not the environment value—into the manifest input.
8. Verify every workload commit tag and every infrastructure digest through the registry.
9. Render `compose.yaml` plus `compose.prod.yaml` in a host-isolated environment, collect the resolved image set, and require it to exactly equal the manifest image set.
10. Publish the canonical manifest, sanitized image file, registry inspection, rendered Compose configuration, resolved Compose image list, and `SHA256SUMS` as one authoritative artifact.

A failed workflow that copied one or more immutable component tags but did not publish the canonical artifact is incomplete and must not be deployed. Releases are serialized repository-wide and an in-progress release is never cancelled by a newer release.

Cross-repository convenience aliases are deliberately not part of automated release completion because four independent registry tags cannot be updated transactionally. If operators choose to run `scripts/publish-release-aliases.sh` manually, those aliases are best-effort navigation aids only and must never be used as production deployment inputs.

## Protected production-release environment

Create a protected GitHub environment named `production-release`, require reviewers, and define these environment variables as immutable references:

- `PRODUCTION_POLICY_IMAGE_DIGEST` — `sha256:<64-hex>` for `docker.io/library/alpine`;
- `PRODUCTION_POSTGRES_IMAGE` — `docker.io/library/postgres@sha256:<64-hex>`;
- `PRODUCTION_REDIS_IMAGE` — `docker.io/library/redis@sha256:<64-hex>`;
- `PRODUCTION_NGINX_IMAGE` — `docker.io/library/nginx@sha256:<64-hex>`;
- `PRODUCTION_MEILI_IMAGE` — `docker.io/getmeili/meilisearch@sha256:<64-hex>`;
- `PRODUCTION_EUROOFFICE_IMAGE` — `ghcr.io/euro-office/documentserver@sha256:<64-hex>`;
- `PRODUCTION_SEAWEEDFS_IMAGE` — the independently reviewed expected SeaweedFS digest, `docker.io/chrislusf/seaweedfs@sha256:<64-hex>`.

The finalizer rejects missing, mutable, malformed, or nonexistent values. The SeaweedFS expectation must equal the digest already exercised by both live suites. Workload references come only from the promoted-digest outputs of the platform scan workflows.

## Local deployment preparation

Copy `deploy/production-images.env.example` to an access-controlled release file and replace every placeholder. The parser accepts only the documented release keys; runtime settings, secrets, `export` assignments, quoting, whitespace tricks, duplicates, and unknown variables are rejected.

`COMPOSE_PROFILES=` is valid and represents the supported production topology using external PostgreSQL and external S3/R2. Profile-only image variables are forbidden unless their corresponding profile is enabled. Any valid combination of `postgres`, `seaweedfs-prod`, and `selfhost-worker` is accepted.

From the exact release commit, after logging Docker into every required private registry, run:

```bash
./scripts/prepare-production-release.sh /secure/path/production-images.env
```

The command:

- rejects modified or staged tracked files;
- writes and uses a sanitized image-only environment file;
- removes host variables before Compose interpolation;
- remotely verifies every digest;
- requires workload `sha-<commit>` tags to resolve to the recorded digest;
- requires exactly `linux/amd64` and `linux/arm64` for workload manifests;
- records SHA-256 hashes of `compose.yaml` and `compose.prod.yaml` in the manifest;
- requires the Compose image set to equal the manifest image set;
- writes the manifest, rendered Compose configuration, resolved Compose images, registry inspection, and checksums.

Operational settings and secrets remain in the normal runtime `.env`; they are never copied into the image manifest file.

## Repository controls

Configure the `main` ruleset to require pull requests and the stable `CI / required` check, block force pushes and deletions, and tightly restrict bypasses. In Actions settings, enable **Require actions to be pinned to a full-length commit SHA**. Dependabot is configured to propose controlled GitHub Actions updates.
