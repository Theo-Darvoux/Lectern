FROM --platform=$BUILDPLATFORM node:22-alpine@sha256:16e22a550f3863206a3f701448c45f7912c6896a62de43add43bb9c86130c3e2 AS base
# Corepack reads the cryptographically hashed `packageManager` field from package.json.
RUN corepack enable

FROM base AS deps
WORKDIR /app
COPY package.json pnpm-lock.yaml ./
# BuildKit cache mount keeps the pnpm store across builds, so a lockfile change
# only downloads the packages that actually changed.
RUN --mount=type=cache,id=pnpm-store,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile

# Building on top of deps (rather than base) reuses node_modules and the pnpm
# binary Corepack already downloaded there, instead of fetching it on every build.
FROM deps AS builder
COPY . .
# Only build facts are baked into the bundle. Instance settings (branding,
# public URLs) are read from the API on every request by the Next server, so
# one image serves every deployment. NEXT_PUBLIC_* values are inlined at build
# time; left unset they bake as "" and the app falls back to its in-code
# default via `|| ...` (e.g. NEXT_PUBLIC_API_URL -> "/api"). release.yml
# supplies NEXT_PUBLIC_COMMIT_SHA.
ARG NEXT_PUBLIC_COMMIT_SHA
ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_COMMIT_SHA=$NEXT_PUBLIC_COMMIT_SHA \
    NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_TELEMETRY_DISABLED=1
# Persist webpack's incremental cache between builds; a small source change then
# rebuilds only the affected modules instead of the whole bundle.
RUN --mount=type=cache,id=next-cache,target=/app/.next/cache \
    pnpm build

# `output: "standalone"` traces the server's dependencies into .next/standalone,
# so the runtime image carries no pnpm store or dev dependencies.
FROM node:22-alpine@sha256:16e22a550f3863206a3f701448c45f7912c6896a62de43add43bb9c86130c3e2 AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static
COPY --from=builder --chown=node:node /app/public ./public
USER node
EXPOSE 3000
# A static file: proves the server is up without rendering a page (which calls the API).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget --quiet --spider http://127.0.0.1:3000/favicon.ico || exit 1
CMD ["node", "server.js"]
