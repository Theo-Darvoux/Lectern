/**
 * Runtime-agnostic delivery handler shared by both deployments:
 *
 *   - Cloudflare Worker  (src/index.ts)   — object source = R2 binding,
 *                                            edge cache    = caches.default
 *   - Self-hosted Node   (src/server.ts)  — object source = S3 (SeaweedFS/
 *                                            Garage/RustFS), cache = none
 *                                            (nginx proxy_cache fronts it)
 *
 * Everything here uses only Web-standard APIs available in both workerd and
 * Node 20+ (URL, Headers, Request, Response, ReadableStream, DecompressionStream,
 * crypto.subtle). The two runtime-specific concerns — where bytes come from and
 * whether there is an in-process edge cache — are injected via {@link HandlerDeps}.
 *
 * The HMAC token contract is identical to the API's app/core/worker_token.py, so
 * switching the API's WORKER_ZIP_URL between the two deployments needs no code
 * change.
 */

import { downloadZip } from "client-zip";

interface ZipEntry {
  arcname: string;
  r2_key: string;
}

export interface TokenPayload {
  exp: number;

  // ZIP fields
  dir_name?: string;
  entries?: ZipEntry[];
  part?: number;
  total?: number;

  // Single file fields
  r2_key?: string;
  force_download?: boolean;
  filename?: string;
  content_type?: string;
}

/** A stored object, normalised across R2 and S3. */
export interface StoredObject {
  body: ReadableStream;
  size?: number;
  /** Value of the stored Content-Encoding metadata (e.g. "gzip"), if any. */
  contentEncoding?: string;
  etag?: string;
  /** Write the object's stored HTTP metadata (at least Content-Type) onto headers. */
  writeHttpMetadata(headers: Headers): void;
}

/** Where object bytes come from — R2 binding or an S3 client. */
export interface ObjectSource {
  get(key: string): Promise<StoredObject | null>;
}

/** Minimal structural type for an edge cache (Cloudflare's caches.default). */
export interface EdgeCache {
  match(request: Request): Promise<Response | undefined>;
  put(request: Request, response: Response): Promise<void>;
}

export interface HandlerDeps {
  source: ObjectSource;
  secret: string;
  /** Optional in-process edge cache. Omitted on Node (nginx caches instead). */
  cache?: EdgeCache | null;
  /** Defer a background task (Cloudflare ctx.waitUntil). Defaults to fire-and-forget. */
  waitUntil?: (promise: Promise<unknown>) => void;
}

function b64urlDecode(s: string): ArrayBuffer {
  const b64 = s.replace(/-/g, "+").replace(/_/g, "/");
  const padded = b64 + "==".slice(0, (4 - (b64.length % 4)) % 4);
  const raw = atob(padded);
  const buf = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) buf[i] = raw.charCodeAt(i);
  return buf.buffer;
}

export async function verifyToken(
  token: string,
  secret: string,
): Promise<TokenPayload | null> {
  const dot = token.lastIndexOf(".");
  if (dot === -1) return null;

  const payloadPart = token.slice(0, dot);
  const sigPart = token.slice(dot + 1);

  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["verify"],
  );

  let sig: ArrayBuffer;
  try {
    sig = b64urlDecode(sigPart);
  } catch {
    return null;
  }

  const valid = await crypto.subtle.verify(
    "HMAC",
    key,
    sig,
    new TextEncoder().encode(payloadPart),
  );
  if (!valid) return null;

  let payload: TokenPayload;
  try {
    // b64urlDecode re-adds stripped padding and returns raw bytes.
    // TextDecoder handles non-ASCII dir_name values (e.g. French accents).
    const payloadBytes = new Uint8Array(b64urlDecode(payloadPart));
    payload = JSON.parse(new TextDecoder().decode(payloadBytes));
  } catch {
    return null;
  }

  if (payload.exp < Date.now() / 1000) return null;
  return payload;
}

export async function handleRequest(
  request: Request,
  deps: HandlerDeps,
): Promise<Response> {
  const { source, secret, cache } = deps;
  const waitUntil = deps.waitUntil ?? ((p: Promise<unknown>) => void p.catch(() => {}));
  const url = new URL(request.url);

  const isBranding = url.pathname.startsWith("/branding/");
  if (
    !url.pathname.startsWith("/zip") &&
    !url.pathname.startsWith("/file/") &&
    !isBranding
  ) {
    return new Response("Not found", { status: 404 });
  }

  // CORS preflight
  if (request.method === "OPTIONS") {
    return new Response(null, {
      headers: {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET",
        "Access-Control-Max-Age": "86400",
      },
    });
  }

  if (request.method !== "GET") {
    return new Response("Method not allowed", { status: 405 });
  }

  // ==========================================
  // PUBLIC ROUTE: Branding assets (no token)
  // ==========================================
  if (isBranding) {
    const key = url.pathname.slice(1); // strip leading /  →  branding/logo.webp
    const object = await source.get(key);
    if (!object) return new Response("Not found", { status: 404 });

    const headers = new Headers();
    object.writeHttpMetadata(headers);
    headers.set("Cache-Control", "public, max-age=86400");
    headers.set("Access-Control-Allow-Origin", "*");

    return new Response(object.body, { headers });
  }

  const token = url.searchParams.get("token");
  if (!token) {
    return new Response("Missing token", { status: 401 });
  }

  const payload = await verifyToken(token, secret);
  if (!payload) {
    return new Response("Invalid or expired token", { status: 401 });
  }

  // ==========================================
  // Single File Secure Caching
  // ==========================================
  if (url.pathname.startsWith("/file/")) {
    const cacheUrl = new URL(request.url);
    cacheUrl.search = "";
    const cacheKey = new Request(cacheUrl.toString(), request);

    let response = cache ? await cache.match(cacheKey) : undefined;

    if (!response) {
      if (!payload.r2_key) {
        return new Response("Invalid token payload for file", { status: 400 });
      }
      const object = await source.get(payload.r2_key);

      if (!object) return new Response("Not found", { status: 404 });

      const isGzip = object.contentEncoding === "gzip";

      const headers = new Headers();
      object.writeHttpMetadata(headers);
      if (object.etag) headers.set("etag", object.etag);
      // Force edge caching for 1 month
      headers.set("Cache-Control", "public, max-age=2592000");

      // Handle overrides
      if (payload.content_type) {
        headers.set("Content-Type", payload.content_type);
      }

      if (payload.filename) {
        const encodedName = encodeURIComponent(payload.filename);
        const asciiFallback = payload.filename.replace(/[^\x20-\x7E]/g, "_");
        const disposition = payload.force_download ? "attachment" : "inline";
        headers.set(
          "Content-Disposition",
          `${disposition}; filename="${asciiFallback}"; filename*=UTF-8''${encodedName}`,
        );
      } else if (payload.force_download) {
        headers.set("Content-Disposition", "attachment");
      } else {
        headers.set("Content-Disposition", "inline");
      }

      // Decompress gzip on fresh fetches so the browser receives raw bytes.
      // We check stored metadata directly (not the HTTP header) because an edge
      // cache can strip the gzip body while keeping the content-encoding header,
      // which would cause double-decompression garbage on cached responses.
      let body: ReadableStream = object.body;
      if (isGzip) {
        body = body.pipeThrough(new DecompressionStream("gzip"));
        headers.delete("content-encoding");
        headers.delete("content-length");
      }

      headers.set("Access-Control-Allow-Origin", "*");

      response = new Response(body, { headers });

      // Cache the already-decompressed response so cache hits are safe.
      if (cache) {
        waitUntil(cache.put(cacheKey, response.clone()));
      }
    }

    // Cache hit: serve directly. The cached response is already decompressed.
    const headers = new Headers(response.headers);
    headers.set("Access-Control-Allow-Origin", "*");
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers,
    });
  }

  // ==========================================
  // ZIP Generation
  // ==========================================
  if (url.pathname === "/zip") {
    if (!payload.entries || !payload.dir_name) {
      return new Response("Invalid token payload for zip", { status: 400 });
    }

    const entries = payload.entries;
    const dirName = payload.dir_name;

    // Async generator so object fetches happen one at a time (lazy) but each
    // yielded item has a concrete ReadableStream — client-zip does not accept
    // functions.
    async function* streamFiles() {
      for (const { arcname, r2_key } of entries) {
        const obj = await source.get(r2_key);
        if (!obj) {
          // Key missing — skip rather than yielding a corrupt empty file.
          console.error(`ZIP: missing key ${r2_key}, skipping ${arcname}`);
          continue;
        }
        let input = obj.body;
        let size: number | undefined = obj.size;
        if (obj.contentEncoding === "gzip") {
          input = obj.body.pipeThrough(new DecompressionStream("gzip"));
          size = undefined;
        }
        yield { name: arcname, input, size };
      }
    }

    const files = streamFiles();

    // Part suffix is omitted for part 1 so a single-part download has a clean
    // filename; subsequent parts append " (N)" for disambiguation.
    const suffix =
      payload.part && payload.total && payload.total > 1 && payload.part > 1
        ? ` (${payload.part})`
        : "";
    const baseName =
      dirName.replace(/[^\x20-\x7E]/g, "_").replace(/\//g, "_") || "directory";
    const asciiFallback = baseName + suffix;
    const encodedName = encodeURIComponent(dirName + suffix);
    const disposition = `attachment; filename="${asciiFallback}.zip"; filename*=UTF-8''${encodedName}.zip`;

    const zipResponse = downloadZip(files);

    return new Response(zipResponse.body, {
      headers: {
        "Content-Type": "application/zip",
        "Content-Disposition": disposition,
        "Access-Control-Allow-Origin": "*",
      },
    });
  }

  return new Response("Not found", { status: 404 });
}
