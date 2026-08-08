/**
 * Cloudflare Worker entry point.
 *
 * Thin adapter: it wraps the R2 bucket binding as an {@link ObjectSource} and
 * the global edge cache as the handler's {@link EdgeCache}, then defers to the
 * runtime-agnostic {@link handleRequest} in handler.ts (shared with the
 * self-hosted Node deployment in server.ts).
 */

import {
  handleRequest,
  ObjectSource,
  RangeNotSatisfiableError,
  StoredObject,
} from "./handler.js";

export interface Env {
  BUCKET: R2Bucket;
  HMAC_SECRET: string;
}

/** Adapt the R2 bucket binding to the runtime-agnostic ObjectSource. */
function r2Source(bucket: R2Bucket): ObjectSource {
  return {
    async get(key: string, rangeHeader?: string): Promise<StoredObject | null> {
      const object = await bucket.get(
        key,
        rangeHeader ? { range: new Headers({ Range: rangeHeader }) } : undefined,
      );
      if (!object) {
        // R2 returns null for both a missing key and an unsatisfiable range.
        // Distinguish them so callers receive RFC-correct 416 rather than 404.
        if (rangeHeader) {
          const head = await bucket.head(key);
          if (head) throw new RangeNotSatisfiableError(head.size);
        }
        return null;
      }
      if (rangeHeader) {
        const match = /^bytes=(\d*)-(\d*)$/.exec(rangeHeader);
        const startsPastEnd = Boolean(match?.[1]) && BigInt(match![1]) >= BigInt(object.size);
        const emptySuffix = !match?.[1] && match?.[2] === "0";
        if (startsPastEnd || emptySuffix) {
          await object.body.cancel();
          throw new RangeNotSatisfiableError(object.size);
        }
      }
      let contentRange: string | undefined;
      let responseSize = object.size;
      if (rangeHeader && object.range) {
        const range = object.range;
        let start: number;
        let length: number;
        if ("suffix" in range) {
          length = Math.min(range.suffix, object.size);
          start = object.size - length;
        } else {
          start = range.offset ?? 0;
          length = range.length ?? object.size - start;
        }
        responseSize = length;
        contentRange = `bytes ${start}-${start + length - 1}/${object.size}`;
      }
      return {
        body: object.body,
        size: responseSize,
        contentEncoding: object.httpMetadata?.contentEncoding,
        etag: object.httpEtag,
        contentRange,
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
