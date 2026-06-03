# WikINT

Course-materials platform for Telecom SudParis / IMT-BS. Students and staff upload, browse, annotate, and collaboratively review documents through a pull-request workflow.

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

## Quick start

```bash
cp .env.example .env      # fill in required values

docker compose up         # dev: compose.yaml + compose.override.yaml are merged automatically
```

The app is available at `http://localhost` (Nginx on port 80). MinIO (local S3) is started automatically in dev.

For production deployment, see [docs/setup.md : Option B](docs/setup.md#option-b--production-deployment):

```bash
docker compose -f compose.yaml -f compose.prod.yaml up -d
```

For a full local dev setup (running components individually, seeding the database, env var reference), see [docs/setup.md](docs/setup.md).

## Documentation

| Doc | What it covers |
|---|---|
| [docs/setup.md](docs/setup.md) | Local development setup from scratch |
| [docs/upload-pipeline.md](docs/upload-pipeline.md) | File upload flow: tus → CAS → scanner → ARQ |
| [docs/pull-requests.md](docs/pull-requests.md) | Collaborative PR workflow for material changes |
| [docs/adr/](docs/adr/) | Architecture Decision Records |

## Project layout

```
api/                  FastAPI backend, workers, migrations
web/                  Next.js frontend
worker/               Cloudflare Worker (HMAC-signed R2 access, ZIP offload)
stress-tests/         k6 load test suite
infra/                Nginx configs, Docker init scripts
compose.yaml          Base service definitions (all environments)
compose.override.yaml Dev overlay, auto-merged (MinIO, hot reload, source mounts)
compose.prod.yaml     Prod overlay, explicit (prebuilt images, gunicorn, resource limits)
```

## Contributing

All changes to materials and directories go through the pull-request workflow, see [docs/pull-requests.md](docs/pull-requests.md).
