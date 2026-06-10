# Installation & Deployment

This page covers getting the platform running — locally for development, and in
production. For *what each setting does*, see the
[Configuration guide](configuration.md) and the
[Environment Variables Reference](environment-variables.md).

## Prerequisites

- Docker + Docker Compose (for the full stack)
- Python 3.12 + [uv](https://github.com/astral-sh/uv) (API only)
- Node.js 20+ + pnpm (frontend only)

---

## Compose file structure

The project uses a base **`compose.yaml`** (optimized for development) and overlays production configurations with **`compose.prod.yaml`** using the Docker Compose `-f` flags.

Two mechanisms handle environment differences:

- **Profiles** — optional service groups started by setting `COMPOSE_PROFILES`
  in `.env`. The `seaweedfs-dev` profile (default in `.env.example`) starts the
  local SeaweedFS single-node storage. The `seaweedfs-prod` profile starts the
  production cluster. The `selfhost-worker` profile adds the self-hosted
  HMAC-signed delivery worker.
- **Production Overrides** — production specific server commands (Gunicorn), 
  build targets (runner stage), resource limits, and production Nginx config templates 
  are configured in `compose.prod.yaml`. Production deployments merge both files.

---

## Option A : Full stack development environment with docker compose

```bash
# 1. Copy and fill the env file (single file at project root)
cp .env.example .env

# 2. Start all services — COMPOSE_PROFILES=seaweedfs-dev is the default
docker compose up
```

What the default dev configuration provides:
- **SeaweedFS** (`seaweedfs-dev` profile): local S3-compatible storage, bucket
  auto-created by the one-shot `seaweedfs-setup` container.
- **uvicorn `--reload`** for the API, **`next dev --turbopack`** for the frontend.
- Source bind-mount on `./web` so the Next.js dev server reads live source.
- **Hot-reload via file-sync** (Docker Compose v2.22+): `docker compose up --watch`
  syncs changes in `./api/app` and `./web/src` directly into the running
  containers without restarting them.
- Dev Nginx config (`infra/nginx/nginx.dev.conf.template`) with inline CORS
  and EuroOffice routing.

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

Production uses pre-built images from GHCR. Production deployments merge the base `compose.yaml` with the production overrides in `compose.prod.yaml`:

```bash
# Minimal production .env additions (on top of the defaults in .env.example):
ENVIRONMENT=production
COMPOSE_PROFILES=          # empty = no local storage containers; uses R2 or external S3
WORKER_FAST_REPLICAS=2
WORKER_SLOW_REPLICAS=2
```

Then deploy:

```bash
# Pull the latest production images from GHCR
docker compose -f compose.yaml -f compose.prod.yaml pull

# Start services in production mode
docker compose -f compose.yaml -f compose.prod.yaml up -d
```

What production mode changes relative to the dev defaults:
- All services use published images (`ghcr.io/theo-darvoux/lectern/api:latest`, etc.)
- API runs under **gunicorn** with 4 uvicorn workers (defined in `compose.prod.yaml`)
- Web is served by **nginx** from the pre-built static export (64 MB container, defined in `compose.prod.yaml`)
- `ENVIRONMENT=production` activates secret validation, hides OpenAPI docs, changes logging
- Resource limits are set per service (defined in `compose.prod.yaml`)
- `worker-fast` and `worker-slow` scale horizontally via `WORKER_FAST_REPLICAS` / `WORKER_SLOW_REPLICAS`
- Nginx exposes port `9080` (defined in `compose.prod.yaml`; put a reverse proxy or load balancer in front)
- No local storage container; `STORAGE_BACKEND` points at Cloudflare R2 or a self-hosted S3 backend

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

# Start backing services from the root (seaweedfs-dev profile for local storage)
docker compose --profile seaweedfs-dev up postgres redis meilisearch seaweedfs seaweedfs-setup -d

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

**SeaweedFS bucket missing in dev** : the `seaweedfs-setup` one-shot container creates the bucket on first start. If it failed, run `docker compose up seaweedfs-setup` again (the `seaweedfs-dev` profile must be active, i.e. `COMPOSE_PROFILES=seaweedfs-dev` in `.env`).
