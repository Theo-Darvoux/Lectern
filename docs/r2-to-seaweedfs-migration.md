# R2 → SeaweedFS Migration Runbook

This document describes the step-by-step process for migrating WikINT's
production storage from Cloudflare R2 to self-hosted SeaweedFS. The migration
uses the lossless backup system (v2.0) built into the admin panel.

---

## Prerequisites

- [ ] SeaweedFS prod server provisioned (two disks/volumes minimum)
- [ ] `compose.prod.seaweedfs.yaml` deployed and `seaweedfs-s3` healthy
- [ ] `s3.json` rendered from template with prod credentials:
  ```sh
  envsubst < infra/docker/seaweedfs/s3.json.template > /opt/seaweedfs/s3.json
  ```
- [ ] `selfhost-worker` image built and pushed to registry
- [ ] nginx `worker-cache.conf` enabled in prod nginx config
- [ ] DNS/proxy for `files.wikint.hypnos2026.fr` updated to point at selfhost-worker

---

## Migration Steps

### Step 1 — Freeze Uploads (Optional but Recommended)

Disable new uploads at the API level to prevent objects being written to R2
during the transfer window. Either:
- Set an env var `MAINTENANCE=true` and deploy a maintenance response, or
- Simply proceed during low-traffic hours (any uploads during transfer are lost)

### Step 2 — Take a Lossless Backup from R2

With `STORAGE_BACKEND=r2` still active in production:

```sh
# Via the admin panel:
POST /api/admin/backup/save

# Or via curl (replace TOKEN):
curl -X POST https://api.wikint.hypnos2026.fr/api/admin/backup/save \
  -H "Authorization: Bearer $TOKEN"
```

Download the resulting ZIP:

```sh
curl -O https://api.wikint.hypnos2026.fr/api/admin/backup/{id}/download \
  -H "Authorization: Bearer $TOKEN"
```

**Verify the ZIP**: open it and confirm `s3_metadata.json` is present and
`manifest.json` shows `"version": "2.0"`.

### Step 3 — Deploy SeaweedFS Stack

```sh
# Render credentials
envsubst < infra/docker/seaweedfs/s3.json.template > /opt/seaweedfs/s3.json

# Bring up the storage stack
docker compose -f compose.yaml -f compose.prod.seaweedfs.yaml up -d \
  seaweedfs-master seaweedfs-volume1 seaweedfs-volume2 seaweedfs-filer seaweedfs-s3

# Wait for S3 gateway to be healthy
docker compose ps seaweedfs-s3
```

Create the bucket (runs once):

```sh
docker compose -f compose.yaml -f compose.prod.seaweedfs.yaml run --rm seaweedfs-setup
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
S3_BUCKET=wikint
```

Redeploy the API and worker containers (they now point at SeaweedFS).

### Step 5 — Restore the Backup to SeaweedFS

```sh
# Via the admin panel → "Upload backup" → select the ZIP from Step 2
POST /api/admin/backup/restore/upload

# Or via curl:
curl -X POST https://api.wikint.hypnos2026.fr/api/admin/backup/restore/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@backup_YYYYMMDD_HHMMSS.zip"
```

This:
1. Wipes existing DB rows (full replacement)
2. Re-inserts all 24 tables in FK order
3. Wipes all S3 prefixes on SeaweedFS
4. Re-uploads every object with its original `Content-Type`, `Content-Encoding`,
   `Content-Disposition` headers

### Step 6 — Smoke Test

```sh
# Presigned GET — should return original Content-Type
curl -I "<presigned-url>"

# Worker file serve — should decompress gzip correctly
curl -I "https://files.wikint.hypnos2026.fr/file/<key>?token=..."

# Branding assets — publicly readable
curl -I "https://files.wikint.hypnos2026.fr/branding/logo.png"

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
- [ ] SeaweedFS running with replication=001 (2 volume nodes)
- [ ] `s3.json` uses prod credentials (not minioadmin)
- [ ] API `.env` updated to `STORAGE_BACKEND=seaweedfs`
- [ ] Restore completed without errors
- [ ] Smoke tests pass (presigned GET, worker serve, branding, upload)
- [ ] selfhost-worker deployed and nginx `worker-cache.conf` active
- [ ] DNS updated to selfhost-worker (not Cloudflare Worker)
- [ ] R2 bucket retained for 48 h before deletion (rollback window)
