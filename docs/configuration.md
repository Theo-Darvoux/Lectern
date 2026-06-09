# Configuration

This guide is task-oriented: pick the thing you want to change, follow the
steps. Every setting is a variable in the root `.env` file — for the full,
alphabetised list with defaults, see the
[Environment Variables Reference](environment-variables.md).

After editing `.env`, apply changes by restarting the affected services:

```bash
# dev
docker compose up -d

# prod
docker compose -f compose.yaml -f compose.prod.yaml up -d
```

A handful of branding/auth settings can also be edited live in the **Admin
Dashboard** without a restart. Where `.env` and the dashboard disagree, `.env`
wins and the dashboard shows the value read-only — that way your declared config
is always the source of truth.

---

## First-run essentials

Before anything else works in production, set these:

1. **`SECRET_KEY`** — `openssl rand -hex 32`. Rotating it logs everyone out.
2. **Database** — `POSTGRES_PASSWORD` and a matching `DATABASE_URL`.
3. **`MEILI_MASTER_KEY`** — any long random string.
4. **Storage** — `STORAGE_BACKEND` plus the `S3_*` credentials for it.
5. **EuroOffice** — `EUROOFFICE_JWT_SECRET` and `EUROOFFICE_FILE_TOKEN_SECRET`,
   **two different** random strings.
6. **`FRONTEND_URL`** — your real public origin (e.g. `https://app.example.com`).

The app will refuse to start in production if any of the critical secrets are
still at their placeholder values, or if the two EuroOffice secrets match. That
guard is deliberate.

---

## Choosing a storage backend

The platform talks S3 to everything; you pick the implementation with
`STORAGE_BACKEND`. Per-backend quirks are handled for you in code, so switching
is just env changes.

| Goal | Set |
|---|---|
| **Local dev** | `STORAGE_BACKEND=seaweedfs` (this is the compose default — SeaweedFS runs automatically). |
| **Cloudflare R2** | `STORAGE_BACKEND=r2`, point `S3_ENDPOINT`/`S3_ACCESS_KEY`/`S3_SECRET_KEY` at your R2 bucket, and set `S3_USE_SSL=true`. |
| **Self-hosted (Garage / RustFS / SeaweedFS in prod)** | the matching `STORAGE_BACKEND` value plus its `S3_*` endpoint and keys. |

If the browser reaches storage on a different address than the API does
(common behind a reverse proxy), set `S3_PUBLIC_ENDPOINT` to the browser-facing
one.

### Faster, edge-cached downloads (optional)

To serve single-file downloads and branding assets through an HMAC-signed,
edge-cached worker instead of presigned S3 URLs, set `WORKER_ZIP_URL` and
`WORKER_ZIP_HMAC_SECRET` (the secret must match the worker's). It works with
both the Cloudflare Worker and the self-hosted Node worker — same token
contract, so you only change the URL. Leave `WORKER_ZIP_URL` empty to fall back
to presigned S3 / server-side streaming.

---

## Setting up sign-in

The platform supports several login methods; turn on the ones you want.

- **Google** — `GOOGLE_OAUTH_ENABLED=true` and `GOOGLE_CLIENT_ID=<your id>`.
- **Email + password** — `CLASSIC_AUTH_ENABLED=true`.
- **Two-factor (TOTP)** — on by default (`TOTP_ENABLED=true`).
- **Guest browsing** — `GUEST_ACCESS_ENABLED=true` for read-only access without
  an account.

### Restricting who can register

Only configured email domains may sign up. **A fresh install ships with no
allowed domains, so registration is blocked until you configure at least one** —
this is a required setup step. You have three options:

1. **Set `ALLOWED_DOMAINS`** to a comma-separated list of `domain:auto` /
   `domain:manual` entries:

   ```dotenv
   ALLOWED_DOMAINS=example.com:auto,example.org:manual
   ```

   - `auto` — approved automatically on first login.
   - `manual` — created but held until a staff member approves them.

2. **Leave `ALLOWED_DOMAINS` empty and manage the list from the admin UI**
   (**Authentication → Domains**). The first admin (created via the first-run
   setup screen below) is exempt from the domain check, so you can always
   bootstrap an instance and add domains from there.

3. **Accept *any* domain** by setting `ALLOW_ALL_DOMAINS=true` (and optionally
   `AUTO_APPROVE_ALL_DOMAINS=true`).

If none of these is configured, no one other than the first admin can register.

### Creating the first admin

On a fresh instance with no admin account, open the app in a browser: you are
redirected to a **first-run setup screen** that prompts you to create the initial
administrator — email, password (8+ characters), and an optional display name —
with the `bureau` role. The screen is only reachable while no admin exists; once
one does, the setup endpoint permanently returns a conflict and the screen
redirects to the login page.

Prefer the CLI? You can still seed an admin directly:

```bash
docker compose exec api uv run python -m app.cli seed --email you@yourorg.com
```

This promotes (or creates) that user to the `bureau` role. Need to log someone
in without sending mail? `... app.cli magic-link you@yourorg.com`.

Typed the wrong email during setup? Fix it without recreating the account:

```bash
docker compose exec api uv run python -m app.cli change-email wrong@yourorg.com you@yourorg.com
```

---

## Email (SMTP)

Verification mails and magic links need a working SMTP server:

```dotenv
SMTP_HOST=smtp.yourprovider.com
SMTP_PORT=587
SMTP_USER=postmaster@yourorg.com
SMTP_PASSWORD=...
SMTP_FROM=no-reply@yourorg.com
SMTP_USE_TLS=true
SMTP_SENDER_NAME=Lectern
```

If outbound DNS is flaky, pin the server IP with `SMTP_IP` while keeping
`SMTP_HOST` for the TLS certificate name.

---

## Branding the instance

Make it look like your school, not the default. The common knobs:

```dotenv
SITE_NAME=My Course Wiki
SITE_DESCRIPTION=Lecture notes and past exams
PRIMARY_COLOR=#7c3aed
SITE_LOGO_URL=https://cdn.example.com/logo.svg
SITE_FAVICON_URL=https://cdn.example.com/favicon.ico
OG_IMAGE_URL=https://cdn.example.com/og.png
FOOTER_TEXT=© 2026 My School
ORGANIZATION_URL=https://www.myschool.edu
```

There's also a background watermark (`BG_WATERMARK_URL` + the two opacity
values) and an advanced multi-color wordmark (`SITE_NAME_STYLE`).

### Designing the wordmark (`SITE_NAME_STYLE`)

`SITE_NAME_STYLE` makes the site name in the navbar render as styled segments —
each with its own text, font, color, and bold/italic — instead of plain text.
The value is a JSON array, which is awkward to write by hand, so the admin
dashboard ships a visual builder for it.

**In the dashboard:** sign in as an admin and open **Config → Branding →
Wordmark Builder**. Add a segment per styled piece of the name (e.g. `Lect` +
`ern`), pick a font and color, toggle bold/italic, and watch the live preview.
The builder generates the exact line to set:

```dotenv
SITE_NAME_STYLE=[{"text":"Lect","font":"Inter","color":"#7c3aed","bold":true,"italic":false},{"text":"ern","font":"Inter","color":null,"bold":true,"italic":false}]
```

Copy it with the **Copy to Clipboard** button, paste it into your `.env`
(replacing any existing `SITE_NAME_STYLE` line), and restart the stack
(`docker compose up -d`) to apply it. Notes:

- The whole admin Config screen is **read-only** — it reflects what's in the
  environment and can't write it back, so applying a wordmark always means
  editing `.env` and restarting. The builder only produces the value for you.
- Each segment's `font` must be one of the fonts the builder offers (Inter,
  Poppins, Playfair Display, …); the public site auto-loads the
  Google Fonts for whichever ones the wordmark uses. A `color` of `null` (or an
  omitted color) inherits the default gradient.
- Don't wrap the value in quotes — both Docker Compose and the API read the raw
  JSON as-is. If the JSON is malformed, the site silently falls back to plain
  `SITE_NAME`.

Fill in the legal/GDPR block (`LEGAL_NAME`, `CONTACT_EMAIL`, `DPO_EMAIL`, …) to
populate the legal-notice and privacy pages.

---

## Tuning uploads and file safety

- **Bigger files** — raise `MAX_FILE_SIZE_MB` and the relevant per-category cap
  (`MAX_VIDEO_SIZE_MB`, `MAX_DOCUMENT_SIZE_MB`, …). The client automatically
  fetches and displays the correct limit dynamically from the backend configuration.
- **Restrict file types** — `ALLOWED_EXTENSIONS=.pdf,.docx` and/or
  `ALLOWED_MIME_TYPES`. Empty = allow everything.
- **Smaller stored files** — lower `PDF_QUALITY`, pick a heavier
  `VIDEO_COMPRESSION_PROFILE`, or shrink `THUMBNAIL_SIZE_PX`.
- **Malware policy** — `MALWAREBAZAAR_FAIL_CLOSED=true` rejects uploads when the
  MalwareBazaar API is unreachable; `false` lets YARA stay the gate. After
  updating YARA rules, lower `CAS_MAX_AGE_SECONDS` so cached-clean files get
  re-scanned.

---

## Scaling for load

Throughput for uploads is `replicas × max-jobs` per queue. Small files go to the
fast queue, large/video files to the slow queue:

```dotenv
WORKER_FAST_REPLICAS=4
WORKER_FAST_MAX_JOBS=4
WORKER_SLOW_REPLICAS=2
WORKER_SLOW_MAX_JOBS=2
```

Replica counts are applied by `compose.prod.yaml`. Keep slow-queue concurrency
modest — video compression is CPU- and memory-hungry, and the per-service
resource limits in the prod compose file assume that.

---

## Observability

- **Metrics** — Prometheus scrapes `GET /metrics`. Inside a private network you
  can leave it open; otherwise set `METRICS_TOKEN` and scrape with
  `?token=<value>`.
- **Tracing** — point `OTEL_ENDPOINT` at your OTLP collector (e.g.
  `localhost:4317`) to export OpenTelemetry traces. Empty disables it.
- **Health** — `GET /api/health` is an unauthenticated liveness check.

---

## Where to go next

- [Environment Variables Reference](environment-variables.md) — every variable,
  with defaults.
- [Installation & Deployment](setup.md) — getting the stack running in dev and
  prod.
