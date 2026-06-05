FROM node:22-alpine AS base
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
# The commit SHA identifies the running web image. It must be present as env at
# build time so Next.js inlines it into the static export. Note: we deliberately
# do NOT declare ARGs for the other NEXT_PUBLIC_* vars — leaving them undefined
# lets the app fall back to its defaults (e.g. NEXT_PUBLIC_API_URL -> "/api").
ARG NEXT_PUBLIC_COMMIT_SHA
ENV NEXT_PUBLIC_COMMIT_SHA=$NEXT_PUBLIC_COMMIT_SHA
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
