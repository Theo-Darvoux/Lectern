# SeaweedFS Migration & Modular Storage Plan

## Implementation status (2026-06-05)

- **Phase 1 — `StorageBackend` refactor: DONE.** `api/app/core/storage/` package
  (`base.py` quirks + Protocol, `s3.py` `S3Backend`, `backends.py` R2/SeaweedFS/
  Garage/RustFS, `__init__.py` facade + `get_storage()`). `STORAGE_BACKEND` config
  added (default `r2`). Zero call-site changes. Full API suite green (1435 passed),
  `mypy app/` and `ruff` clean.
- **Phase 2 — `Delivery` seam + self-hosted worker: DONE.**
  `storage/delivery.py` (`WorkerDelivery` / `DirectDelivery` / `get_delivery`),
  wired into `generate_presigned_get` + `get_public_url` (new tests in
  `tests/core/test_storage_delivery.py`). Worker logic extracted to runtime-agnostic
  `worker/src/handler.ts`; `index.ts` is now a thin Cloudflare adapter (25 workerd
  tests still green); self-hosted Node port `worker/src/node/{server,s3-source}.ts`
  (+ `Dockerfile`, `tsconfig.node.json`, 12 Node-runtime handler tests green,
  `tsc -p tsconfig.node.json` clean).
- **Phase 3 — compose + docs: DONE (opt-in form).** `compose.seaweedfs.yaml`
  overlay (SeaweedFS + bootstrap + `selfhost-worker`), `infra/docker/seaweedfs/`
  (`s3.json`, `setup.sh`), `infra/nginx/worker-cache.conf`, `.env.example` +
  `CLAUDE.md` updated. MinIO is intentionally **not** removed yet — see below.
- **Phase 0 — Validation spike: RUN & PASSED (2026-06-05).** Validated against a
  standalone `chrislusf/seaweedfs:latest` (S3 gateway :8333) with the real storage
  layer. All checks green:
  - put / head / read parity, copy / move / list / delete parity ✓
  - presigned PUT end-to-end (HTTP 200) ✓
  - **presigned PUT + `ChecksumSHA256`: ACCEPTED (HTTP 200)** — SeaweedFS validates
    SHA256 checksums, so no skip flag is needed; `SeaweedFSBackend` keeps the
    conservative `when_required` default.
  - presigned multipart >5 MiB (12 MiB / 2 parts, exact size) ✓; list/abort multipart ✓
  - **Self-hosted worker delivery loop** (server.ts + s3-source.ts) against SeaweedFS:
    `/file` signed → bytes ✓; gzip object → decompressed, no `content-encoding` (no
    double-decompress) ✓; `/branding` anonymous → bytes ✓; bad token → 401; missing
    key → 404. Confirms SeaweedFS surfaces `ContentEncoding` correctly.
  - **Remaining manual step (not destructive):** the final MinIO removal from
    `compose.override.yaml` — deferred to a deliberate cutover. Everything needed is
    in `compose.seaweedfs.yaml`; switch is `STORAGE_BACKEND=seaweedfs` +
    `S3_ENDPOINT=seaweedfs:8333`.

---

> Goal: make WikINT's object storage backend-agnostic so production can move off
> Cloudflare R2 to a self-hosted S3-compatible store. Primary target: **SeaweedFS**.
> Must stay swappable to **Garage** / **RustFS** later by config, not rewrite.
> Must preserve the Cloudflare "feel": HMAC-signed file delivery, ZIP download,
> public branding assets, gzip decompression, and edge caching.

## Why this shape

MinIO community edition entered maintenance mode (Dec 2025) and its repo was archived
(Apr 2026), so it is not a forward-looking dev/prod choice. SeaweedFS (Apache 2.0,
production-adopted, full S3 API incl. multipart) is the chosen replacement; Garage and
RustFS are kept as future options. All three are S3-compatible, so the `aioboto3`
client works for all of them — the real work is (a) isolating R2-specific quirks and
(b) replacing the Cloudflare Worker's edge-delivery role.

## Current coupling (verified against the code)

**Layer 1 — `api/app/core/storage.py` (S3 client). Low friction.** R2-specific bits:
- `storage.py:22-28` — `request_checksum_calculation/response_checksum_validation="when_required"`:
  workaround for R2 returning bad CRCs. SeaweedFS does NOT need this.
- `storage.py:99-129` `_rewrite_host()` — R2 custom-domain rewriting, "R2 custom domains
  don't support presigned PUT", bucket-prefix stripping. Irrelevant for SeaweedFS.
- `storage.py:763-784` `get_public_url()` — custom-domain / worker URL assembly.
- Config: `api/app/config.py:58-65` (`s3_*`), `:200,:203` (`worker_zip_url`, `worker_zip_hmac_secret`).

**Layer 2 — `worker/src/index.ts` (Cloudflare Worker). The real work.** Bound to the
`R2Bucket` binding + `caches.default`. Four responsibilities:
1. HMAC-token-verified single-file serving — `/file/{key}?token=` (`index.ts:138-205`),
   incl. gzip `DecompressionStream`, content-disposition overrides, 1-month edge cache.
2. Streaming ZIP — `/zip?token=` via `client-zip` (`index.ts:210-263`).
3. Public branding assets — `/branding/*`, no token (`index.ts:112-123`).
4. Edge caching via `caches.default` (`index.ts:143-194`) — beats R2's ~10 Mbps origin cap.

The API already branches: if `settings.worker_zip_url` set → sign token + point at worker
(`storage.py:436-450`); else → presigned S3 GET. **The fallback silently loses ZIP,
branding, gzip-decompress, and edge caching.** So self-hosting must keep the worker's
*contract*, swapping only its storage source + cache.

Token signing lives in `api/app/core/worker_token.py` (`make_file_token`). The token
format must stay identical so the worker-equivalent verifies it unchanged.

## Target architecture — two seams

### Seam 1: `StorageBackend` interface (foundation, no behavior change)
Refactor `storage.py` free functions behind a Protocol + an `S3Backend` base class with
thin per-backend subclasses carrying only quirks. Keep a module-level `storage` facade so
call sites across `routers/` and `services/` don't churn.

```
api/app/core/storage/
  __init__.py        # re-exports facade fns (back-compat shim) + get_storage()
  base.py            # ObjectStorage Protocol + ObjectInfo / StreamBody types
  s3.py              # S3Backend (aioboto3) implementing ObjectStorage
  backends.py        # R2Backend, SeaweedFSBackend, GarageBackend, RustFSBackend
```

`ObjectStorage` Protocol surface (derived from current public fns in storage.py):
- `put_object`, `upload_file`, `upload_file_multipart`
- `get_stream` (ctx mgr), `download_file`, `download_file_with_hash`,
  `read_full_object`, `read_object_bytes`
- `head`/`object_exists`/`get_object_info`, `update_object_content_type`
- `copy`/`move`, `delete`
- `create_multipart`, `upload_part`, `complete_multipart`, `abort_multipart`,
  `presign_upload_part`
- `presign_get` (+ cached variant + bust), `presign_put`
- `list_objects`, `list_multipart_uploads`
- `get_public_url`

Per-backend flags (set in subclasses, consumed by S3Backend):
- `skip_checksum_validation: bool` — True for R2 only.
- `rewrite_presign_host: bool` / custom-domain handling — R2 only.
- `presign_put_supported_on_public_host: bool` — R2 False, SeaweedFS True.
- `multipart_part_size(file_size)` — keep `dynamic_part_size` default.

Factory: `get_storage()` selects on new `settings.storage_backend` ∈
`{r2, seaweedfs, garage, rustfs}` (default `r2` to preserve current behavior).

**Acceptance for Seam 1:** existing tests green, `mypy app/` clean, no call-site
signature changes (facade preserves names; keep `generate_presigned_get` etc. aliases).

### Seam 2: `Delivery` interface + self-hosted worker
Abstract the inline `if worker_zip_url` branch into a `Delivery` strategy:
- `WorkerDelivery` — current Cloudflare path, unchanged.
- `SelfHostedDelivery` — same `make_file_token` contract, base URL = self-hosted worker.

**Self-hosted worker = port `worker/src/index.ts`, don't reimplement in API.** Only two
Cloudflare-specific calls to replace:
- `env.BUCKET.get(key)` (R2 binding) → `fetch()` to SeaweedFS (S3 gateway or native filer
  HTTP). Same code path works for Garage/RustFS (all S3). Abstract behind a small
  `getObject(key)` function so the storage source is swappable.
- `caches.default` → remove; front the container with existing **nginx `proxy_cache`**.

Runtime: small Node HTTP service reusing the existing worker logic (WebCrypto HMAC,
ReadableStream, DecompressionStream, `client-zip` all run on Node 20+). Keep
`worker/src/index.ts` for Cloudflare; add a `worker/src/server.ts` (or `worker-selfhost/`)
entry that wires the same handlers to a Node `http` server + an S3/filer `getObject`.
Decision to confirm at build time: shared module vs. separate dir (lean shared module).

### SeaweedFS compose service
Replace `minio` + `minio-setup` in `compose.override.yaml` (dev) and add to
`compose.prod.yaml`:
- `chrislusf/seaweedfs:latest` `server -dir=/data -s3 -s3.config=...` single binary.
- S3 creds via the same `S3_ACCESS_KEY`/`S3_SECRET_KEY` env already used.
- Bucket bootstrap step (replace `infra/docker/minio/setup.sh` with a SeaweedFS equiv
  creating the `wikint` bucket + the access key/identity).
- Self-hosted worker container + nginx `proxy_cache` location block.

## Phases (execution order)

1. **Phase 0 — Validation spike (do FIRST, throwaway).** Stand up SeaweedFS in compose,
   point `.env` S3 settings at it, and exercise the real upload pipeline. Confirm:
   - presigned PUT (`generate_presigned_put`) works end-to-end.
   - presigned `upload_part` multipart flow works (`generate_presigned_upload_part` →
     complete). Use a >5 MiB file to force multipart.
   - `ChecksumSHA256` on presigned PUT (`storage.py:400-402`): does SeaweedFS accept/verify
     or reject it? Determines whether `SeaweedFSBackend` sets a skip flag.
   - head/get/copy/delete/list parity.
   Gate: if presigned PUT or multipart fails, resolve before proceeding.

2. **Phase 1 — `StorageBackend` refactor.** Pure restructure, no behavior change. Add
   `storage_backend` config (default `r2`). Move R2 quirks into `R2Backend`. Tests + mypy
   green. This is independently mergeable.

3. **Phase 2 — `Delivery` abstraction + self-hosted worker.** Extract delivery strategy;
   port worker to a Node service with swappable `getObject`; nginx `proxy_cache` front.
   Verify `/file`, `/zip`, `/branding`, gzip path, content-disposition, ZIP filename
   (incl. accented dir names, see `index.ts:248-251`).

4. **Phase 3 — Compose + docs.** SeaweedFS services in dev (`compose.override.yaml`) and
   prod (`compose.prod.yaml`); bucket bootstrap; update `.env.example`; document the
   backend switch + nginx cache in `CLAUDE.md` "Infrastructure Notes".

## Risks / watch-items
- **Presigned PUT & multipart on SeaweedFS** — #1 risk, gated in Phase 0.
- **Checksum SHA256** — may need a per-backend skip flag.
- **Edge cache parity** — nginx `proxy_cache` must replicate the 1-month cache + the
  presign-URL-stability trick (`storage.py:480-551`) that makes CDN/edge caching effective.
- **gzip double-decompress** — worker deliberately reads `httpMetadata` not the HTTP header
  (`index.ts:178-187`); preserve this when sourcing from SeaweedFS (it may surface
  content-encoding differently than R2 — verify).
- **`get_public_url` / branding** — ensure branding route still resolves under self-hosted
  delivery.

## Definition of done
- `settings.storage_backend=seaweedfs` runs the full app: upload (incl. >5 MiB multipart),
  download, ZIP, branding, OnlyOffice inline view — with no R2/Cloudflare dependency.
- Switching `r2`/`seaweedfs`/`garage`/`rustfs` is env-only at the storage layer.
- Existing pytest suite green; `mypy app/` clean; `ruff` clean.
- `compose.override.yaml` uses SeaweedFS (MinIO removed); prod path documented.
