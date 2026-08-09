# R2 → SeaweedFS Migration Runbook

This document describes the step-by-step process for migrating the platform's
production storage from Cloudflare R2 to self-hosted SeaweedFS. The migration
uses the lossless backup system (v2.0) built into the admin panel.

---

## Prerequisites

- [ ] SeaweedFS prod server provisioned (two disks/volumes minimum)
- [ ] Canonical production release prepared with the certified `seaweedfs-prod,selfhost-worker` profiles (see [Production release manifest](production-release-manifest.md))
- [ ] `s3.json` rendered from template with prod credentials:
  ```sh
  envsubst < infra/docker/seaweedfs/s3.json.template > /opt/seaweedfs/s3.json
  ```
- [ ] `selfhost-worker` image built and pushed to registry
- [ ] nginx `worker-cache.conf` enabled as the delivery proxy policy (authenticated routes remain uncached)
- [ ] DNS/proxy for `files.example.com` updated to point at selfhost-worker

---

## Migration Steps

### Step 1 — Freeze Mutations (Required)

Put the external reverse proxy into maintenance mode for every mutating API
method before taking the backup. The application has no `MAINTENANCE` setting;
setting an invented environment variable does nothing. Verify with an ordinary
user that an upload initiation and PR creation are rejected, then wait for the
upload queues and open write requests to drain. Uploads accepted after the
backup would be destroyed by the full restore.

### Step 2 — Take a Lossless Backup from R2

With `STORAGE_BACKEND=r2` still active in production:

```sh
# Run inside the stopped API image/container with the production runtime env.
python -m app.cli create-backup-offline \
  --confirm-offline /var/lib/lectern/backups/r2-migration.zip
```

Copy the resulting ZIP to durable operator-controlled storage. Production HTTP
backup creation is intentionally disabled: even with a reverse-proxy mutation
freeze, an in-flight worker could otherwise produce a database/object-store
snapshot from different points in time.

**Verify the ZIP**: open it and confirm `s3_metadata.json` is present,
`manifest.json` shows `"version": "2.0"`, and the manifest contains an
`s3_objects` size/SHA-256 record for every stored object. Restore independently
checks these records before changing the database or object store.

### Step 3 — Deploy SeaweedFS Stack

Prepare and deploy the exact canonical release as described in
[Option B](setup.md#option-b--production-deployment), selecting the
`seaweedfs-prod,selfhost-worker` profiles. Use the generated
`production-<commit>.deployment-images.env` for both startup and later `ps`
commands. Do not export a hand-written `SEAWEEDFS_IMAGE` or start the production
profile from `compose.yaml` alone.

The `seaweedfs-dev` profile's `seaweedfs-setup` one-shot container is not available
in `seaweedfs-prod`. Create the bucket manually if needed:

```sh
aws s3 mb s3://lectern --endpoint-url http://localhost:8333
```

### Step 4 — Switch the API to SeaweedFS

Update the production `.env`:

```env
STORAGE_BACKEND=seaweedfs
S3_ENDPOINT=seaweedfs-s3:8333
S3_PUBLIC_ENDPOINT=<your-server-ip-or-domain>/s3
S3_USE_SSL=false
S3_ACCESS_KEY=<prod-key>
S3_SECRET_KEY=<prod-secret>
S3_BUCKET=lectern
```

Redeploy the API and worker containers (they now point at SeaweedFS).

### Step 5 — Restore the Backup to SeaweedFS

```sh
# Keep API/workers stopped and run inside the new API image/container.
python -m app.cli restore-backup-offline \
  --confirm-offline /var/lib/lectern/backups/r2-migration.zip
```

This destructive full replacement:
1. Fully validates and reads the archive within configured storage bounds
2. Replaces all 24 database tables in one database transaction
3. Creates server-side rollback copies of the current managed S3 objects
4. Re-uploads every backup object with its original `Content-Type`,
   `Content-Encoding`, and `Content-Disposition` headers
5. Deletes stale managed objects and removes the rollback copies only after the
   object replacement succeeds

An object-store failure during replacement restores the server-side snapshot.
Keep the mutation freeze active until the command has completed and the smoke
tests pass; database and S3 cannot participate in one distributed transaction.

### Step 6 — Smoke Test

```sh
# Presigned GET — should return original Content-Type
curl -I "<presigned-url>"

# Worker file serve — should decompress gzip correctly
curl -I "https://files.example.com/file/<key>?token=..."

# Branding assets — publicly readable
curl -I "https://files.example.com/branding/logo.png"

# Upload — creates a new object in SeaweedFS
# (test via the UI or the API upload endpoint)
```

### Step 7 — Remove R2 (After Confirming Everything Works)

- Wait 24–48 h for CDN caches to expire
- Delete the R2 bucket from the Cloudflare dashboard
- Remove `WORKER_ZIP_HMAC_SECRET` from wrangler.toml if the Cloudflare Worker
  is no longer deployed

---

## Rollback

If anything goes wrong before Step 6 completes, switch back instantly:

```env
STORAGE_BACKEND=r2
S3_ENDPOINT=<r2-endpoint>
S3_ACCESS_KEY=<r2-key>
S3_SECRET_KEY=<r2-secret>
```

Redeploy — R2 was never wiped.

---

## Checklist Summary

- [ ] Backup ZIP downloaded and verified (v2.0, s3_metadata.json present)
- [ ] SeaweedFS running with replication=010 (2 volume nodes in separate racks)
- [ ] `s3.json` uses prod credentials (not minioadmin)
- [ ] API `.env` updated to `STORAGE_BACKEND=seaweedfs`
- [ ] Restore completed without errors
- [ ] Smoke tests pass (presigned GET, worker serve, branding, upload)
- [ ] selfhost-worker deployed and nginx `worker-cache.conf` active
- [ ] DNS updated to selfhost-worker (not Cloudflare Worker)
- [ ] R2 bucket retained for 48 h before deletion (rollback window)
