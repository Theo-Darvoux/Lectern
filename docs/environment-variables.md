# Environment Variables Reference

Every setting WikINT understands lives in a single `.env` file at the project
root. There are no per-component env files — the API, the workers, the frontend
build, and the Docker Compose stack all read the same file.

Start by copying the template and editing it:

```bash
cp .env.example .env
```

This page is the exhaustive reference. If you just want to get something
specific done ("turn on Google login", "change the logo", "raise the upload
limit"), read the task-oriented [Configuration guide](configuration.md) first —
it tells you which variables to touch and links back here for the details.

## How to read this page

- **Required** variables must have a real value before the app will start (or
  start *correctly*) in production. Most have safe-ish dev defaults baked into
  the code, but you should never ship those.
- **Default** is the value the API falls back to when the variable is unset
  (from `api/app/config.py`). A blank default means "unset / empty".
- Anything marked **secret** must be a long random string. Generate one with:

  ```bash
  openssl rand -hex 32      # or: python -c "import secrets; print(secrets.token_hex(32))"
  ```

- In production the app runs a **startup validator** that refuses to boot if
  certain secrets are still set to their placeholder values, or if the two
  EuroOffice secrets are identical. This is intentional — it stops you from
  accidentally deploying with `change-me` secrets.

> **Naming note:** the document-editing integration is wired through
> `EUROOFFICE_*` variables everywhere the code actually reads them
> (`api/app/config.py`, all three compose files, and `.env.example`). If you have
> an older `.env` file using legacy `ONLYOFFICE_*` keys, rename those keys to `EUROOFFICE_*`
> so they are recognized.

---

## Core

| Variable | Default | Description |
|---|---|---|
| `ENVIRONMENT` | `development` | `development`, `production`, or `test`. Production turns on the secret validator, hides the OpenAPI docs, and changes logging. Compose forces `production` on every service in `compose.prod.yaml`. |
| `SECRET_KEY` | *(dev placeholder)* | **Required, secret.** Signs JWTs and derives the CAS HMAC. Changing it invalidates every existing session. |
| `FRONTEND_URL` | `http://localhost:3000` | Public base URL of the frontend. Used for CORS and for links inside emails. Set this to your real origin in production (e.g. `https://wikint.example.com`). |
| `RUN_MIGRATIONS` | `true` | When `true`, the `api` container runs `alembic upgrade head` on startup. Set to `false` to skip migrations (e.g. to apply them out-of-band during a maintenance window). If a migration fails, the API prints a banner and refuses to start rather than running against a broken schema. |

---

## PostgreSQL

The first four are consumed by the `postgres` container; `DATABASE_URL` is what
the API actually connects with. Keep them consistent.

| Variable | Default | Description |
|---|---|---|
| `POSTGRES_USER` | `wikint` | Database role created on first boot. |
| `POSTGRES_PASSWORD` | *(dev placeholder)* | **Required.** Database password. |
| `POSTGRES_DB` | `wikint` | Database name. |
| `POSTGRES_HOST` | `postgres` | Hostname — the compose service name inside Docker. |
| `POSTGRES_PORT` | `5432` | Port. |
| `DATABASE_URL` | `postgresql+asyncpg://wikint:wikint@localhost:5432/wikint` | **Required.** Async SQLAlchemy connection string. Must use the `postgresql+asyncpg://` scheme. In Docker the host is `postgres`, not `localhost`. |

---

## Redis

| Variable | Default | Description |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379/0` | **Required.** Backs caching, rate limiting, and the ARQ job queues. In Docker the host is `redis`. |

---

## Search (MeiliSearch)

| Variable | Default | Description |
|---|---|---|
| `MEILI_URL` | `http://localhost:7700` | MeiliSearch base URL (`http://meilisearch:7700` in Docker). |
| `MEILI_MASTER_KEY` | `change-me` | **Required in prod, secret.** Admin key. The startup validator rejects the literal `change-me` in production. |
| `MEILI_SEARCH_KEY` | *(unset)* | Search-only key used by the public search route. Provision it via the MeiliSearch admin API. If unset, the app falls back to the master key with a warning — acceptable in dev, not in prod. |

---

## Object storage (S3-compatible)

WikINT is storage-backend-agnostic: every backend speaks S3, and per-backend
quirks (checksum mode, presigned-PUT host rewriting) are handled automatically
in `api/app/core/storage/backends.py`. Switching backends is env-only.

| Variable | Default | Description |
|---|---|---|
| `STORAGE_BACKEND` | `r2` | One of `r2` (Cloudflare R2), `seaweedfs`, `garage`, `rustfs`. Dev compose ships `seaweedfs`. |
| `S3_ENDPOINT` | `localhost:9000` | Internal S3 endpoint the API talks to (`seaweedfs:8333` in dev). |
| `S3_PUBLIC_ENDPOINT` | *(unset)* | Endpoint the browser uses, when it differs from the internal one (e.g. `localhost/s3` in dev). |
| `S3_PUBLIC_DOMAIN` | *(unset)* | Public domain for serving R2 assets through the prod Nginx S3 proxy (e.g. `files.wikint.example.com`). |
| `S3_ACCESS_KEY` | `minioadmin` | **Required.** Access key. |
| `S3_SECRET_KEY` | `minioadmin` | **Required, secret.** Secret key. |
| `S3_BUCKET` | `wikint` | Bucket name. |
| `S3_REGION` | `us-east-1` | Region. For R2 this is effectively `auto`/ignored. |
| `S3_USE_SSL` | `false` | `true` when the endpoint is HTTPS. |
| `S3_USE_ACCELERATE_ENDPOINT` | `false` | Enable S3 Transfer Acceleration (AWS S3 only). |
| `MAX_STORAGE_GB` | `10` | Total storage quota in GB across all materials. |

### Signed delivery via the Worker (optional)

When set, single-file downloads and branding assets are served through an
HMAC-signed, edge-cached worker instead of presigned S3 GETs. Point it at
**either** the Cloudflare Worker **or** the self-hosted Node worker — the token
contract is identical, so switching is URL-only.

| Variable | Default | Description |
|---|---|---|
| `WORKER_ZIP_URL` | *(unset)* | Worker base URL. Leave empty to fall back to presigned S3 / server-side streaming. |
| `WORKER_ZIP_HMAC_SECRET` | *(unset)* | **Secret.** Must match the secret configured on the worker (`wrangler secret put HMAC_SECRET`, or the env var on the Node worker). |

---

## Document editing (EuroOffice)

| Variable | Default | Description |
|---|---|---|
| `EUROOFFICE_JWT_SECRET` | *(dev placeholder)* | **Required in prod, secret.** Shared with the EuroOffice document server. |
| `EUROOFFICE_FILE_TOKEN_SECRET` | *(dev placeholder)* | **Required in prod, secret.** Signs short-lived file-access tokens. **Must differ from `EUROOFFICE_JWT_SECRET`** so a compromised EuroOffice container cannot forge download tokens — the validator enforces this. |
| `EUROOFFICE_FILE_TOKEN_TTL` | `60` | Lifetime (seconds) of a file-access token. |
| `EUROOFFICE_INTERNAL_API_BASE_URL` | `http://api:8000` | URL the EuroOffice container uses to fetch files from the API (internal network address). |

---

## Authentication

| Variable | Default | Description |
|---|---|---|
| `TOTP_ENABLED` | `true` | Allow TOTP (authenticator-app) two-factor login. |
| `GOOGLE_OAUTH_ENABLED` | `false` | Enable "Sign in with Google". |
| `GOOGLE_CLIENT_ID` | *(unset)* | Google OAuth client ID. Required when Google login is on. |
| `CLASSIC_AUTH_ENABLED` | `false` | Enable classic email + password login. |
| `GUEST_ACCESS_ENABLED` | `false` | Allow unauthenticated read-only browsing. |
| `ALLOW_ALL_DOMAINS` | `false` | Accept sign-ups from any email domain (otherwise only `ALLOWED_DOMAINS`). |
| `AUTO_APPROVE_ALL_DOMAINS` | `false` | Auto-approve every new account instead of holding them for staff review. |
| `ALLOWED_DOMAINS` | *(empty)* | Comma-separated `domain:auto` / `domain:manual` entries, e.g. `telecom-sudparis.eu:auto,imt-bs.eu:manual`. `auto` = approved on first login, `manual` = needs staff approval. When set, this overrides the editable `allowed_domains` DB table (the admin UI then shows it read-only). Empty = use the DB table. |
| `JWT_ACCESS_TOKEN_EXPIRE_DAYS` | `7` | Access-token lifetime. |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | `31` | Refresh-token lifetime. |

---

## Email (SMTP)

Used for verification emails, magic links, and notifications.

| Variable | Default | Description |
|---|---|---|
| `SMTP_HOST` | `smtp.example.com` | SMTP server hostname. |
| `SMTP_PORT` | `587` | SMTP port. |
| `SMTP_USER` | *(empty)* | SMTP username. |
| `SMTP_PASSWORD` | *(empty)* | **Secret.** SMTP password. |
| `SMTP_FROM` | *(empty)* | `From:` address on outgoing mail. |
| `SMTP_USE_TLS` | `true` | Use STARTTLS. |
| `SMTP_IP` | *(unset)* | Connect to this IP directly instead of resolving `SMTP_HOST` (handy when DNS is unreliable). |
| `SMTP_SENDER_NAME` | *(unset)* | Display name in the `From:` header (e.g. `WikINT`). |
| `SMTP_AVATAR_URL` | *(unset)* | Avatar image shown in verification emails. |

---

## Branding

All optional — sensible defaults ship in the code. Most of these can also be
edited live in the admin dashboard.

| Variable | Default | Description |
|---|---|---|
| `SITE_NAME` | `WikINT` | Site name shown in the UI and page titles. |
| `SITE_DESCRIPTION` | `Wiki for SudParis Intelligence` | Meta description / tagline. |
| `SITE_NAME_STYLE` | *(unset)* | Advanced: JSON array of styled name segments for a multi-color wordmark. |
| `PRIMARY_COLOR` | `#3b82f6` | Accent color (hex). |
| `FOOTER_TEXT` | `© 2024 WikINT` | Footer text. |
| `ORGANIZATION_URL` | `https://www.telecom-sudparis.eu` | Link behind the organization name. |
| `SITE_LOGO_URL` | *(unset)* | Logo image URL. |
| `SITE_FAVICON_URL` | *(unset)* | Favicon URL. |
| `OG_IMAGE_URL` | *(unset)* | Open Graph preview image. |
| `FOOTER_LOGO_URL` | *(unset)* | Logo shown in the footer. |
| `BG_WATERMARK_URL` | *(unset)* | Background watermark image. |
| `BG_WATERMARK_OPACITY_LIGHT` | *(unset)* | Watermark opacity in light mode (0–1). |
| `BG_WATERMARK_OPACITY_DARK` | *(unset)* | Watermark opacity in dark mode (0–1). |

---

## Legal / GDPR

All optional. Populate them to fill in the legal-notice and privacy pages.

| Variable | Default | Description |
|---|---|---|
| `LEGAL_NAME` | *(unset)* | Legal entity name. |
| `LEGAL_ADDRESS` | *(unset)* | Registered address. |
| `LEGAL_SIRET` | *(unset)* | SIRET / company registration number. |
| `CONTACT_EMAIL` | *(unset)* | Public contact address. |
| `DPO_EMAIL` | *(unset)* | Data Protection Officer email. |
| `DPO_ADDRESS` | *(unset)* | DPO postal address. |
| `DATA_TRANSFERS` | *(EU-hosting statement, in French)* | Free-text statement about international data transfers. |
| `LEGAL_VERSION` | `1.0` | Version stamp for the legal terms. |

---

## File pipeline & upload limits

Size caps are enforced server-side **before** transfer/processing. All sizes are
in MiB unless noted.

| Variable | Default | Description |
|---|---|---|
| `MAX_FILE_SIZE_MB` | `100` | Global hard cap for any single file. |
| `MAX_SVG_SIZE_MB` | `5` | Per-category cap for SVGs. |
| `MAX_IMAGE_SIZE_MB` | `50` | Per-category cap for images. |
| `MAX_AUDIO_SIZE_MB` | `200` | Per-category cap for audio. |
| `MAX_VIDEO_SIZE_MB` | `500` | Per-category cap for video. |
| `MAX_DOCUMENT_SIZE_MB` | `200` | Per-category cap for PDFs/documents. |
| `MAX_OFFICE_SIZE_MB` | `100` | Per-category cap for Office files. |
| `MAX_TEXT_SIZE_MB` | `20` | Per-category cap for text/code. |
| `ALLOWED_EXTENSIONS` | *(empty = all)* | Comma-separated allowlist, e.g. `.pdf,.docx`. |
| `ALLOWED_MIME_TYPES` | *(empty = all)* | Comma-separated MIME-type allowlist. |
| `PDF_QUALITY` | `75` | PDF compression quality (0–100). |
| `PDF_COMPRESSION_LEVEL` | *(unset)* | Ghostscript-style alias (`screen`/`ebook`/`printer`/`prepress`); when set it overrides `PDF_QUALITY`. |
| `VIDEO_COMPRESSION_PROFILE` | `medium` | ffmpeg profile: `none`, `light`, `medium`, `aggressive`, `heavy`, `extreme`. |
| `THUMBNAIL_QUALITY` | `85` | Thumbnail JPEG quality (0–100). |
| `THUMBNAIL_SIZE_PX` | `640` | Thumbnail longest-edge size in pixels. |

### tus resumable-upload internals

Rarely need touching; defaults match S3 multipart constraints.

| Variable | Default | Description |
|---|---|---|
| `UPLOAD_PIPELINE_MAX_SECONDS` | `600` | Hard deadline for the whole worker pipeline per upload. |
| `TUS_CHUNK_MIN_BYTES` | `5 MiB` | Minimum chunk size (S3 multipart minimum). |
| `TUS_CHUNK_MAX_BYTES` | `100 MiB` | Maximum chunk size. |
| `TUS_MAX_SIZE_BYTES` | `500 MiB` | Maximum resumable upload size. |
| `TUS_MAX_CONCURRENT_PER_USER` | `8` | Concurrent in-flight uploads per user. |
| `ENABLE_PRESIGNED_MULTIPART` | `true` | Use presigned multipart PUTs for large uploads. |
| `DIRECT_UPLOAD_THRESHOLD_MB` | `10` | Files below this use a single direct upload instead of multipart. |
| `CAS_MAX_AGE_SECONDS` | `604800` (7 days) | How long a CAS entry is trusted before re-scanning. Bump it down after changing YARA rules. `0` disables staleness checks. |

---

## Malware scanning

| Variable | Default | Description |
|---|---|---|
| `YARA_RULES_DIR` | `yara_rules` | Directory of compiled YARA rules. |
| `YARA_SCAN_TIMEOUT` | `60` | Seconds before a YARA scan is aborted. |
| `MALWAREBAZAAR_URL` | `https://mb-api.abuse.ch/api/v1/` | MalwareBazaar API endpoint. |
| `MALWAREBAZAAR_API_KEY` | *(unset)* | Optional API key to avoid stricter rate limits. |
| `MALWAREBAZAAR_TIMEOUT` | `5` | Request timeout in seconds. |
| `MALWAREBAZAAR_FAIL_CLOSED` | `true` | When `true`, a MalwareBazaar error/timeout fails the scan (rejects the upload). When `false`, YARA stays the authoritative gate on API failure. |
| `BAZAAR_ASYNC_ENABLED` | `true` | Run the MalwareBazaar check as a background job after YARA-only promotion (avoids ~6 s of blocking per upload). YARA remains the synchronous gate; flags trigger retroactive quarantine. |
| `BAZAAR_RETROACTIVE_CHECK_MATERIALS` | `true` | When a retroactive flag fires, also soft-delete approved material versions referencing the flagged S3 key. |

---

## Pull-request limits

| Variable | Default | Description |
|---|---|---|
| `PR_MAX_OPS_STUDENT` | `50` | Max operations per PR for students. |
| `PR_MAX_OPS_STAFF` | `500` | Max operations per PR for staff. |
| `PR_MAX_ATTACHMENTS_PER_MATERIAL` | `50` | Max attachments per material in a PR. |
| `PR_MAX_OPEN_PER_USER` | `5` | Max simultaneously open PRs per user. |
| `PR_EXPIRY_DAYS` | `7` | Days before an inactive PR expires. |
| `PR_REVERT_GRACE_DAYS` | `7` | Window during which a merged PR can be reverted. |

---

## Scaling & worker concurrency

`WORKER_FAST_REPLICAS` / `WORKER_SLOW_REPLICAS` are read by `compose.prod.yaml`
to scale containers; the `*_MAX_JOBS` values control concurrency *inside* each
replica. Total throughput = replicas × max-jobs.

| Variable | Default | Description |
|---|---|---|
| `WORKER_FAST_REPLICAS` | `2` | Number of `worker-fast` containers (small files). |
| `WORKER_FAST_MAX_JOBS` | `4` | Concurrent jobs per fast worker (I/O-bound, safe to over-subscribe). |
| `WORKER_SLOW_REPLICAS` | `2` | Number of `worker-slow` containers (large/video files). |
| `WORKER_SLOW_MAX_JOBS` | `2` | Concurrent jobs per slow worker (CPU-heavy, keep low). |
| `GLOBAL_MAX_SUBPROCESSES` | `0` (auto) | Cap on heavy subprocesses; `0` = `os.cpu_count()`. |
| `MAX_CONCURRENT_IMAGE_OPS` | `0` (auto) | Cap on concurrent image operations; `0` = `cpu_count // 2`. |

---

## Observability

| Variable | Default | Description |
|---|---|---|
| `METRICS_TOKEN` | *(empty)* | When set, `/metrics` requires `?token=<value>` or a bearer token. Empty = unauthenticated scraping (fine inside a private network). |
| `OTEL_ENDPOINT` | *(empty)* | OTLP collector endpoint for OpenTelemetry traces, e.g. `localhost:4317`. Empty disables export. |

---

## Networking & CORS

| Variable | Default | Description |
|---|---|---|
| `CORS_ALLOWED_HEADERS` | *(a sensible default list)* | Comma-separated, no spaces. Shared between FastAPI and Nginx. |

---

## Webhooks & backups

| Variable | Default | Description |
|---|---|---|
| `WEBHOOK_SECRET` | *(derived from `SECRET_KEY`)* | **Secret.** HMAC-SHA256 signing secret for outgoing webhooks. Set explicitly to rotate independently of the JWT secret. |
| `BACKUP_DIR` | `/var/lib/wikint/backups` | Where the API writes backups (mounted as a volume in compose). |

---

## Frontend (build-time)

These are `NEXT_PUBLIC_*` values baked into the frontend at build/serve time, so
they are visible to the browser. Don't put secrets here.

| Variable | Default | Description |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `/api` | Browser-facing API path. Dev compose sets `http://localhost/api`. |
| `NEXT_PUBLIC_EUROOFFICE_URL` | *(unset)* | Browser-facing EuroOffice path. Dev compose sets `http://localhost/eurooffice`. |
| `NEXT_PUBLIC_MAX_FILE_SIZE_MB` | *(unset)* | Client-side upload-size hint; mirror `MAX_FILE_SIZE_MB`. |
| `NEXT_PUBLIC_COMMIT_SHA` | *(unset)* | Build commit SHA, shown in the About panel. Usually injected by CI. |

---

## Capturing live config before an upgrade

Some settings used to live in the database (the admin dashboard now shows them
read-only). Before upgrading an existing instance, export the current DB values
as `.env`-ready lines so nothing silently reverts to a default:

```bash
docker compose exec api uv run python -m app.cli config-export-env
```
