# Installation & Deployment

This page covers getting WikINT running — locally for development, and in
production. For *what each setting does*, see the
[Configuration guide](configuration.md) and the
[Environment Variables Reference](environment-variables.md).

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
| `compose.override.yaml` | Dev additions: SeaweedFS, source bind-mounts, hot reload, port exposure | Automatically merged on `docker compose up` |
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
| eurooffice | — | EuroOffice document server (internal only) |
| worker | — | ARQ worker (non-upload tasks + fallback) |
| worker-fast | — | ARQ worker for small uploads (text, images) |
| worker-slow | — | ARQ worker for large uploads (video, PDF, Office) |

Database migrations run automatically when the `api` container starts (see the [migrations note](#database-migrations) below), so there's nothing to apply by hand.

To create your first account, open the app in a browser — you are redirected to a **first-run setup screen** that prompts you to create the initial administrator account (email, password, and an optional display name). This only appears while no admin exists.

---

## Option B : Production deployment

Production uses pre-built images from GHCR. **Do not use `compose.override.yaml`** — the prod file must be specified explicitly. Set `COMPOSE_FILE` once so every `docker compose` command picks up both files automatically (no repeated `-f` flags):

```bash
export COMPOSE_FILE=compose.yaml:compose.prod.yaml

docker compose up -d
```

> Add the `export` line to your shell profile (or the `.env` file Compose reads) so it persists across sessions.

What `compose.prod.yaml` changes relative to the base:
- All services use published images (`ghcr.io/theo-darvoux/wikint/api:latest`, etc.)
- API runs under **gunicorn** with 4 uvicorn workers instead of single-process uvicorn
- Workers run with explicit ARQ settings classes (`UploadFastWorkerSettings`, etc.)
- `ENVIRONMENT=production` is forced on all services
- Resource limits and reservations are set per service
- `worker-fast` and `worker-slow` support horizontal scaling via `WORKER_FAST_REPLICAS` / `WORKER_SLOW_REPLICAS`
- Nginx exposes port `9080` externally (put a reverse proxy or load balancer in front)
- No local storage container; production points `STORAGE_BACKEND` at Cloudflare R2 or a self-hosted S3 backend

After first deploy, there are no commands to run. The `api` container applies database migrations automatically on startup (see [Database migrations](#database-migrations) below), and your first account is created through the **first-run setup screen**: open the site in a browser and it will prompt you to create the initial administrator account. That screen disappears once an admin exists.

### Database migrations

Migrations run automatically when the `api` container starts. To run them out-of-band instead (for example, to apply them during a controlled maintenance window), set `RUN_MIGRATIONS=false` on the `api` service and apply them yourself:

```bash
docker compose exec api uv run alembic upgrade head
```

If a migration fails on startup, the API prints a large banner to its logs and **refuses to start** rather than running against a broken schema — fix the migration and redeploy. With the default `restart: unless-stopped` policy the container will keep retrying (re-printing the banner) until the migration succeeds.

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
uv run uvicorn app.main:app --reload --port 8000
```

Then open the frontend and complete the first-run setup screen to create your admin account. (You can still create one from the CLI with `uv run python -m app.cli seed --email admin@example.com` if you prefer.)

### Frontend

```bash
cd web
pnpm install
pnpm dev                          # starts on port 3000
```

All configuration for the frontend comes from the root `.env` file (via `NEXT_PUBLIC_*` variables). In development, API routing is handled by the dev Nginx config in `infra/nginx/nginx.dev.conf.template`.

---

## Environment variables

All variables live in a single `.env` at the project root — there are no
per-component env files. Copy the template and fill it in:

```bash
cp .env.example .env
```

The bare minimum to boot in production: `SECRET_KEY`, `POSTGRES_PASSWORD` +
`DATABASE_URL`, `MEILI_MASTER_KEY`, the `S3_*` storage credentials, two distinct
EuroOffice secrets, and your `FRONTEND_URL`. The app refuses to start
in production while critical secrets are still placeholders.

- **[Configuration guide](configuration.md)** — task-oriented: turn on Google
  login, change the logo, raise upload limits, scale workers, …
- **[Environment Variables Reference](environment-variables.md)** — every
  variable, grouped, with defaults.

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
