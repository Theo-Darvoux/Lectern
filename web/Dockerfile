FROM --platform=$BUILDPLATFORM node:22-alpine@sha256:16e22a550f3863206a3f701448c45f7912c6896a62de43add43bb9c86130c3e2 AS base
# Corepack reads the cryptographically hashed `packageManager` field from package.json.
RUN corepack enable

FROM base AS deps
WORKDIR /app
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

FROM base AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* values are inlined into the static export at build time — the
# static prod image has no Node runtime to read them later, so they MUST be
# present as env during `pnpm build`. build.yml supplies these as build-args
# (from GitHub Actions repo variables); any left unset bake as "" and the app
# falls back to its in-code default via `|| ...` (e.g. NEXT_PUBLIC_API_URL -> "/api").
ARG NEXT_PUBLIC_COMMIT_SHA
ARG NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_EUROOFFICE_URL
ARG NEXT_PUBLIC_REPO_URL
ARG NEXT_PUBLIC_SITE_FAVICON_URL
ARG SITE_NAME
ARG SITE_DESCRIPTION
ARG SITE_FAVICON_URL=/favicon.ico
ENV NEXT_PUBLIC_COMMIT_SHA=$NEXT_PUBLIC_COMMIT_SHA \
    NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_EUROOFFICE_URL=$NEXT_PUBLIC_EUROOFFICE_URL \
    NEXT_PUBLIC_REPO_URL=$NEXT_PUBLIC_REPO_URL \
    NEXT_PUBLIC_SITE_NAME=$SITE_NAME \
    NEXT_PUBLIC_SITE_DESCRIPTION=$SITE_DESCRIPTION \
    NEXT_PUBLIC_SITE_FAVICON_URL=${NEXT_PUBLIC_SITE_FAVICON_URL:-$SITE_FAVICON_URL} \
    SITE_NAME=$SITE_NAME \
    SITE_DESCRIPTION=$SITE_DESCRIPTION \
    SITE_FAVICON_URL=$SITE_FAVICON_URL
RUN pnpm build

FROM nginx:alpine@sha256:54f2a904c251d5a34adf545a72d32515a15e08418dae0266e23be2e18c66fefa AS runner
# The reviewed nginx base is digest-pinned. Security updates arrive by an
# explicit reviewed digest refresh rather than an open-ended package upgrade.
COPY --from=builder /app/out /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget --quiet --spider http://localhost/ || exit 1
