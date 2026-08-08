# Self-hosted worker (Node) — the off-Cloudflare equivalent of the Worker.
# Serves HMAC-signed /file, /branding and /zip from an S3-compatible store
# (SeaweedFS / Garage / RustFS). Front it with nginx proxy_cache in production
# to replace Cloudflare's edge cache.
FROM node:22-alpine@sha256:16e22a550f3863206a3f701448c45f7912c6896a62de43add43bb9c86130c3e2

WORKDIR /app

# Install dependencies (cached layer). Only package manifests first.
COPY package.json package-lock.json ./
RUN npm ci

# App sources (handler + node entry). tsx runs TypeScript directly — no build step.
COPY tsconfig.node.json ./
COPY src ./src

ENV PORT=8788
EXPOSE 8788

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD wget --quiet --spider http://127.0.0.1:8788/healthz || exit 1

CMD ["./node_modules/.bin/tsx", "src/node/server.ts"]
