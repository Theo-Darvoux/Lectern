# Local Development Setup

## Prerequisites

- Docker + Docker Compose (for the full stack)
- Python 3.12 + [uv](https://github.com/astral-sh/uv) (API only)
- Node.js 20+ + pnpm (frontend only)
- k6 (optional, for stress tests)

---

## Compose file structure

The project uses three compose files that layer on top of each other:

| File | Purpose | Used when |
|---|---|---|
| `compose.yaml` | Base service definitions shared by all environments | Always |
| `compose.override.yaml` | Dev additions: MinIO, source bind-mounts, hot reload, port exposure | Automatically merged on `docker compose up` |
| `compose.prod.yaml` | Prod additions: pre-built images, gunicorn, resource limits, replicas | Explicit: `-f compose.yaml -f compose.prod.yaml` |

`compose.override.yaml` is a Docker Compose convention, it is merged automatically without needing to be named on the command line. This means `docker compose up` in development picks up both files silently. In production you always name both files explicitly.

---

## Option A : Full stack development environment with docker compose

```bash
# 1. Copy and fill the env file (single file at project root)
cp .env.example .env

# 2. Start all services (compose.yaml + compose.override.yaml are merged automatically)
docker compose up
```

What `compose.override.yaml` adds in development:
- **SeaweedFS** : local S3-compatible storage (auto-configured bucket via `seaweedfs-setup`). Production can use the same (`STORAGE_BACKEND=seaweedfs`) or Cloudflare R2 (`STORAGE_BACKEND=r2`).
- Source bind-mounts on `api/` and `web/` for hot reload
- `uvicorn --reload` for the API, `next dev --turbopack` for the frontend
- Port exposure: API on `8000`, web on `3000`, SeaweedFS S3 on `8333` (filer UI `8888`, master UI `9333`)
- Dev Nginx config (`infra/nginx/nginx.dev.conf.template`) with CORS handling

Services available after `docker compose up`:

| Service | Exposed port | Purpose |
|---|---|---|
| nginx | 80 | Reverse proxy — main entry point |
| api | 8000 | FastAPI backend (direct access, bypasses nginx) |
| web | 3000 | Next.js dev server |
| postgres | 5432 | PostgreSQL 16 |
| redis | 6379 | Cache, rate limiting, ARQ queues |
| meilisearch | 7700 | Full-text search |
| seaweedfs | 8333 / 8888 / 9333 | Local S3 storage / filer UI / master UI |
| eurooffice | — | ONLYOFFICE document server (internal only) |
| worker | — | ARQ worker (non-upload tasks + fallback) |
| worker-fast | — | ARQ worker for small uploads (text, images) |
| worker-slow | — | ARQ worker for large uploads (video, PDF, Office) |

Apply database migrations and seed initial data:

```bash
docker compose exec api uv run alembic upgrade head
docker compose exec api uv run python -m app.cli seed --email admin@example.com
```

---

## Option B : Production deployment

Production uses pre-built images from GHCR. **Do not use `compose.override.yaml`**, the prod file is specified explicitly:

```bash
docker compose -f compose.yaml -f compose.prod.yaml up -d
```

What `compose.prod.yaml` changes relative to the base:
- All services use published images (`ghcr.io/theo-darvoux/wikint/api:latest`, etc.)
- API runs under **gunicorn** with 4 uvicorn workers instead of single-process uvicorn
- Workers run with explicit ARQ settings classes (`UploadFastWorkerSettings`, etc.)
- `ENVIRONMENT=production` is forced on all services
- Resource limits and reservations are set per service
- `worker-fast` and `worker-slow` support horizontal scaling via `WORKER_FAST_REPLICAS` / `WORKER_SLOW_REPLICAS`
- Nginx exposes port `9080` externally (put a reverse proxy or load balancer in front)
- No MinIO; production uses Cloudflare R2 directly

After first deploy:

```bash
docker compose -f compose.yaml -f compose.prod.yaml exec api uv run alembic upgrade head
docker compose -f compose.yaml -f compose.prod.yaml exec api uv run python -m app.cli seed --email admin@yourorg.com
```

---

## Option C : Bare-metal (API + frontend separately)

Useful when you want fast iteration on a single component without rebuilding containers.

### API

```bash
cd api
uv sync                           # install dependencies

# Start backing services from the root
docker compose up postgres redis meilisearch seaweedfs seaweedfs-setup -d

uv run alembic upgrade head       # apply migrations
uv run python -m app.cli seed --email admin@example.com
uv run uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd web
pnpm install
pnpm dev                          # starts on port 3000
```

All configuration for the frontend comes from the root `.env` file (via `NEXT_PUBLIC_*` variables). In development, API routing is handled by the dev Nginx config in `infra/nginx/nginx.dev.conf.template`.

---

## Environment variables

All variables live in a single `.env` at the project root. There are no per-component env files.

### Required (API)

| Variable | Example | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://user:pass@localhost:5432/wikint` | Async SQLAlchemy connection string |
| `REDIS_URL` | `redis://localhost:6379` | Redis connection |
| `SECRET_KEY` | `openssl rand -hex 32` | JWT signing + CAS HMAC derivation |
| `MEILI_MASTER_KEY` | `<random string>` | Meilisearch admin key |
| `MEILI_SEARCH_KEY` | `<random string>` | Meilisearch search-only key |
| `MEILI_URL` | `http://meilisearch:7700` | Meilisearch URL |
| `STORAGE_BACKEND` | `seaweedfs` | Storage backend: `seaweedfs` / `r2` / `garage` / `rustfs` |
| `S3_ENDPOINT` | `seaweedfs:8333` | S3-compatible storage endpoint |
| `S3_ACCESS_KEY` | `minioadmin` | S3 access key |
| `S3_SECRET_KEY` | `minioadmin` | S3 secret key |
| `S3_BUCKET` | `wikint` | Bucket name |
| `FRONTEND_URL` | `http://localhost` | Used for CORS and email links |

### EuroOffice

| Variable | Description |
|---|---|
| `EUROOFFICE_JWT_SECRET` | Shared with the EuroOffice server |
| `EUROOFFICE_FILE_TOKEN_SECRET` | API-only file access tokens, must differ from `EUROOFFICE_JWT_SECRET` |
| `EUROOFFICE_INTERNAL_API_BASE_URL` | Internal URL EuroOffice uses to fetch files (e.g., `http://api:8000`) |

> In production, `EUROOFFICE_JWT_SECRET` and `EUROOFFICE_FILE_TOKEN_SECRET` **must be different**. The startup validator enforces this.

### File scanning

| Variable | Default | Description |
|---|---|---|
| `YARA_RULES_DIR` | `yara_rules` | Path to compiled YARA rule files |
| `MALWAREBAZAAR_FAIL_CLOSED` | `false` | Reject uploads when MalwareBazaar is unreachable |
| `MALWAREBAZAAR_TIMEOUT` | `10` | Seconds before MalwareBazaar request times out |
| `MALWAREBAZAAR_API_KEY` | (no default) | API key for MalwareBazaar |

### Upload limits

| Variable | Default | Description |
|---|---|---|
| `MAX_FILE_SIZE_MB` | `100` | Hard cap for all files |
| `MAX_VIDEO_SIZE_MB` | — | Per-type override |
| `MAX_DOCUMENT_SIZE_MB` | — | Per-type override |
| `MAX_STORAGE_GB` | — | Total storage quota |
| `ALLOWED_EXTENSIONS` | — | Comma-separated allowlist (e.g., `.pdf,.docx`) |

### Production scaling

| Variable | Default | Description |
|---|---|---|
| `WORKER_FAST_REPLICAS` | `2` | Number of `worker-fast` container replicas |
| `WORKER_FAST_MAX_JOBS` | `4` | Concurrent upload jobs per fast worker replica |
| `WORKER_SLOW_REPLICAS` | `2` | Number of `worker-slow` container replicas |
| `WORKER_SLOW_MAX_JOBS` | `2` | Concurrent upload jobs per slow worker replica |
| `S3_PUBLIC_DOMAIN` | — | Cloudflare R2 public domain (prod Nginx S3 proxy) |

### Frontend

| Variable | Description |
|---|---|
| `NEXT_PUBLIC_API_URL` | Browser-facing API path (set to `http://localhost/api` in dev via compose.override.yaml) |
| `NEXT_PUBLIC_EUROOFFICE_URL` | Browser-facing EuroOffice path (set to `http://localhost/eurooffice` in dev) |

---

## Running tests

```bash
# API uses SQLite in-memory, no external services needed
cd api
uv run pytest

# Frontend vitest unit tests
cd web
pnpm test
```

---

## Useful commands

```bash
# API
uv run ruff check --fix .              # lint
uv run ruff format .                   # format
uv run mypy app/                       # type-check
uv run alembic revision --autogenerate -m "description"  # new migration
uv run python -m app.cli reindex       # rebuild Meilisearch index

# Frontend
pnpm lint                              # eslint
pnpm tsc --noEmit                      # type-check
pnpm i18n:check                        # verify all i18n keys present
pnpm generate-api-types                # regenerate api-types.ts (API must be running)
pnpm knip                              # dead-code detection
```

---

## Common issues

**`EUROOFFICE_JWT_SECRET` validation fails at startup** : the two EuroOffice secrets must be distinct values. The defaults in `.env.example` are distinct; check that neither was copied over the other.

**Uploads stuck in `processing_status=pending`** : `worker-fast` / `worker-slow` are not running or can't reach Redis. Check `docker compose logs worker-fast worker-slow` and Redis connectivity.

**`ERR_MALWARE_DETECTED` on clean files** : YARA rules directory is missing or contains overly broad rules. Check `YARA_RULES_DIR`.

**MeiliSearch index out of sync** : re-index with `uv run python -m app.cli reindex`.

**SeaweedFS bucket missing in dev** : the `seaweedfs-setup` one-shot container creates the bucket on first start. If it failed, run `docker compose up seaweedfs-setup` again.
