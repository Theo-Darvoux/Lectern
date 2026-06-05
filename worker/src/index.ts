/**
 * Cloudflare Worker entry point.
 *
 * Thin adapter: it wraps the R2 bucket binding as an {@link ObjectSource} and
 * the global edge cache as the handler's {@link EdgeCache}, then defers to the
 * runtime-agnostic {@link handleRequest} in handler.ts (shared with the
 * self-hosted Node deployment in server.ts).
 */

import { handleRequest, ObjectSource, StoredObject } from "./handler.js";

export interface Env {
  BUCKET: R2Bucket;
  HMAC_SECRET: string;
}

/** Adapt the R2 bucket binding to the runtime-agnostic ObjectSource. */
function r2Source(bucket: R2Bucket): ObjectSource {
  return {
    async get(key: string): Promise<StoredObject | null> {
      const object = await bucket.get(key);
      if (!object) return null;
      return {
        body: object.body,
        size: object.size,
        contentEncoding: object.httpMetadata?.contentEncoding,
        etag: object.httpEtag,
        writeHttpMetadata: (headers: Headers) => object.writeHttpMetadata(headers),
      };
    },
  };
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    return handleRequest(request, {
      source: r2Source(env.BUCKET),
      secret: env.HMAC_SECRET,
      cache: caches.default,
      waitUntil: (p) => ctx.waitUntil(p),
    });
  },
};
