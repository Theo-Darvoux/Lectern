# Production release manifest

The canonical `production-release-<commit>` GitHub Actions artifact is the authoritative **release complete** marker. Component tags or aliases alone do not identify a complete release.

## Automated release sequence

`release.yml` reruns the complete CI workflow, then `build.yml` performs these phases:

1. Build all four candidate images for `linux/amd64` and `linux/arm64`.
2. Run both live SeaweedFS suites before building the API and worker candidates.
3. Scan both child platforms for every candidate.
4. Copy only immutable `sha-<commit>` tags into the release repositories.
5. Wait for API, worker, web, and self-hosted worker promotion to succeed.
6. Verify every workload commit tag and every infrastructure digest through the registry.
7. Publish the checksummed canonical release artifact.
8. Update `latest` or the `alpha-*` convenience alias only after the artifact exists.

A failed workflow that copied one or more immutable component tags but did not publish the canonical artifact is incomplete and must not be deployed. `release.yml` does not cancel an in-progress release when a newer commit arrives.

## Protected production-release environment

Create a protected GitHub environment named `production-release`, require reviewers, and define these environment variables as immutable references:

- `PRODUCTION_POLICY_IMAGE_DIGEST` — `sha256:<64-hex>` for `docker.io/library/alpine`;
- `PRODUCTION_POSTGRES_IMAGE` — `docker.io/library/postgres@sha256:<64-hex>`;
- `PRODUCTION_REDIS_IMAGE` — `docker.io/library/redis@sha256:<64-hex>`;
- `PRODUCTION_NGINX_IMAGE` — `docker.io/library/nginx@sha256:<64-hex>`;
- `PRODUCTION_MEILI_IMAGE` — `docker.io/getmeili/meilisearch@sha256:<64-hex>`;
- `PRODUCTION_EUROOFFICE_IMAGE` — `ghcr.io/euro-office/documentserver@sha256:<64-hex>`;
- `PRODUCTION_SEAWEEDFS_IMAGE` — `docker.io/chrislusf/seaweedfs@sha256:<64-hex>`.

The finalizer rejects missing, mutable, malformed, or nonexistent values. Workload references come only from the promoted-digest outputs of the platform scan workflows.

## Local deployment preparation

Copy `deploy/production-images.env.example` to an access-controlled release file and replace every placeholder. The parser accepts only the documented release keys; runtime settings, secrets, `export` assignments, quoting, whitespace tricks, duplicates, and unknown variables are rejected.

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
- records SHA-256 hashes of `compose.yaml` and `compose.prod.yaml`;
- requires the Compose image set to equal the manifest image set;
- writes the manifest, resolved Compose images, registry inspection, and checksums.

Operational settings and secrets remain in the normal runtime `.env`; they are never copied into the image manifest file.

## Repository controls

Configure the `main` ruleset to require pull requests and the stable `CI / required` check, block force pushes and deletions, and tightly restrict bypasses. In Actions settings, enable **Require actions to be pinned to a full-length commit SHA**. Dependabot is configured to propose controlled GitHub Actions updates.
