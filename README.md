# Lectern

Collaborative course-materials platform. Students and staff upload, browse, annotate, and collaboratively review documents through a pull-request workflow.

## Stack

| Layer | Technology |
|---|---|
| API | FastAPI · SQLAlchemy async · PostgreSQL · Redis/ARQ · Alembic |
| Frontend | Next.js 16 · React 19 · TypeScript · Tailwind v4 · shadcn/ui |
| Storage | Cloudflare R2 (S3-compatible) with CAS deduplication |
| Search | MeiliSearch |
| Document editing | EuroOffice |
| File safety | YARA · MalwareBazaar · pikepdf · oletools |
| Background jobs | ARQ : fast queue (images/text) and slow queue (video/PDF) |

## Prerequisites

- **Docker Engine ≥ 24** with the **Compose v2 plugin** (`docker compose version` should print `v2.20+`)
- **4 GB RAM** minimum (8 GB recommended for video processing)
- **Ports 80** (and 443 if you terminate TLS at the host) available on the machine
- **S3-compatible object storage** — Cloudflare R2, AWS S3, or a self-hosted backend (SeaweedFS is started automatically in dev via Docker Compose)
- A domain name and reverse proxy (nginx, Caddy…) for production; the included `infra/` configs provide a starting point

## Quick start

```bash
cp .env.example .env      # fill in required values

docker compose up         # dev: compose.yaml + compose.override.yaml are merged automatically
```

The app is available at `http://localhost` (Nginx on port 80). SeaweedFS (local S3 storage) is started automatically in dev.

For production deployment, use the canonical release artifact described in
[docs/setup.md : Option B](docs/setup.md#option-b--production-deployment). Do
not deploy mutable tags or invoke the production overlay without its generated
digest file:

```bash
./scripts/prepare-production-release.sh \
  --canonical-manifest /secure/release/production-<commit>.json \
  --runtime-env /secure/runtime/production.env
```

For a full local dev setup (running components individually, seeding the database, env var reference), see [docs/setup.md](docs/setup.md).

## Documentation

| Doc | What it covers |
|---|---|
| [docs/setup.md](docs/setup.md) | Installation & deployment — dev, production, and bare-metal |
| [docs/configuration.md](docs/configuration.md) | How to configure the app: auth, storage, branding, limits, scaling |
| [docs/environment-variables.md](docs/environment-variables.md) | Complete `.env` reference with defaults |
| [docs/upload-pipeline.md](docs/upload-pipeline.md) | File upload flow: tus → CAS → scanner → ARQ |
| [docs/pull-requests.md](docs/pull-requests.md) | Collaborative PR workflow for material changes |
| [docs/adr/](docs/adr/) | Architecture Decision Records |

## Project layout

```
api/                  FastAPI backend, workers, migrations
web/                  Next.js frontend
worker/               Cloudflare Worker (HMAC-signed R2 access, ZIP offload)
infra/                Nginx configs, Docker init scripts
compose.yaml          Base service definitions (all environments)
compose.override.yaml Dev overlay, auto-merged (SeaweedFS, hot reload, source mounts)
compose.prod.yaml     Prod overlay, explicit (prebuilt images, gunicorn, resource limits)
```

## Contributing

All changes to materials and directories go through the pull-request workflow, see [docs/pull-requests.md](docs/pull-requests.md).
