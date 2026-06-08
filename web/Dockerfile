FROM --platform=$BUILDPLATFORM node:22-alpine AS base
# Pin pnpm to match `packageManager` in package.json for reproducible builds.
RUN corepack enable && corepack prepare pnpm@10.32.1 --activate

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
ENV NEXT_PUBLIC_COMMIT_SHA=$NEXT_PUBLIC_COMMIT_SHA \
    NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_EUROOFFICE_URL=$NEXT_PUBLIC_EUROOFFICE_URL \
    NEXT_PUBLIC_REPO_URL=$NEXT_PUBLIC_REPO_URL
RUN pnpm build

FROM nginx:alpine AS runner
# Pull the latest patched OS packages (e.g. libxml2 — XSD-validation DoS) on top
# of whatever the base image shipped, then drop the apk cache to keep the layer small.
RUN apk upgrade --no-cache libxml2
COPY --from=builder /app/out /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget --quiet --spider http://localhost/ || exit 1
