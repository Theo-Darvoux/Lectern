# Self-hosted worker (Node) — the off-Cloudflare equivalent of the Worker.
# Serves HMAC-signed /file, /branding and /zip from an S3-compatible store
# (SeaweedFS / Garage / RustFS). Authenticated routes must not be cached ahead
# of this process because token verification happens here.
FROM node:22-alpine@sha256:16e22a550f3863206a3f701448c45f7912c6896a62de43add43bb9c86130c3e2 AS build

WORKDIR /app

# Install the locked development toolchain only in the build stage.
COPY package.json package-lock.json ./
RUN npm ci

COPY tsconfig.node.json ./
COPY src ./src
RUN ./node_modules/.bin/tsc -p tsconfig.node.json --noEmit false --outDir dist

FROM node:22-alpine@sha256:16e22a550f3863206a3f701448c45f7912c6896a62de43add43bb9c86130c3e2

WORKDIR /app

# The runtime receives only production dependencies and compiled JavaScript.
# Wrangler, Vitest, tsx, TypeScript, and their transitive tools never enter it.
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && \
    npm cache clean --force && \
    rm -rf \
      /root/.npm \
      /opt/yarn-v1.22.22 \
      /usr/local/lib/node_modules/npm \
      /usr/local/lib/node_modules/corepack && \
    rm -f \
      /usr/local/bin/npm \
      /usr/local/bin/npx \
      /usr/local/bin/corepack \
      /usr/local/bin/yarn \
      /usr/local/bin/yarnpkg
COPY --from=build /app/dist ./dist

ENV NODE_ENV=production
ENV PORT=8788
EXPOSE 8788

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD wget --quiet --spider http://127.0.0.1:8788/healthz || exit 1

USER node

CMD ["node", "dist/node/server.js"]
