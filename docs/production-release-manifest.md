# Production image manifest

Production deployments must use `compose.yaml` together with `compose.prod.yaml` and a release-specific image environment file. Every production container, including infrastructure and the policy helper itself, is accepted only as an allowlisted `repository@sha256:<manifest-digest>` reference.

## Prepare a release

1. Copy `deploy/production-images.env.example` to a release-specific, access-controlled file.
2. Resolve the tested manifest digest for every enabled service. Do not retain tags in the final values.
3. Set `COMPOSE_PROFILES` to the profiles that will actually be deployed.
4. From the exact release commit, run:

```bash
./scripts/prepare-production-release.sh /secure/path/production-images.env
```

The command validates the allowlist, uses the repository `.env` file for runtime configuration when present, then applies the image-only file last so reviewed digests cannot be overridden. It runs Compose interpolation and schema validation and writes three non-secret artifacts under `release-manifests/`:

- a JSON manifest containing the Git commit, enabled profiles, exact image references, and source-file checksum;
- the image list resolved by Docker Compose;
- SHA-256 checksums for both artifacts.

Store these artifacts with the release. Rollback uses the former commit and its corresponding image environment file and manifest, rather than resolving tags again.

## Profile-specific requirements

`POSTGRES_IMAGE` is required when the `postgres` profile is enabled. `SEAWEEDFS_IMAGE` is required for `seaweedfs-prod`, and `SELFHOST_WORKER_IMAGE` is required for `selfhost-worker`. Core workload, Redis, Nginx, Meilisearch, EuroOffice, and the Alpine policy-helper digest are always required. The policy helper repository is hard-coded in Compose; only its reviewed digest is configurable, avoiding a circular trust bootstrap.

The release image environment file contains no application secrets. Keep operational secrets in the normal production environment file; do not add them to the image manifest file.
