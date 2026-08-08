import worker from "./index.js";
import { createExecutionContext, env, SELF, waitOnExecutionContext } from "cloudflare:test";
import { describe, expect, it } from "vitest";

// Must match the HMAC_SECRET binding in vitest.config.ts
const TEST_SECRET = "test-hmac-secret";

/**
 * Produces a signed token in the same format the worker expects:
 *   b64url(json_payload).b64url(hmac_sha256_sig)
 */
async function signToken(
  payload: Record<string, unknown>,
  expOffsetSeconds = 3600,
): Promise<string> {
  const full = { ...payload, exp: Math.floor(Date.now() / 1000) + expOffsetSeconds };

  const bytes = new TextEncoder().encode(JSON.stringify(full));
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  const b64 = btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=/g, "");

  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(TEST_SECRET),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sigBuf = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(b64));
  let sigBin = "";
  for (const b of new Uint8Array(sigBuf)) sigBin += String.fromCharCode(b);
  const sig = btoa(sigBin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=/g, "");

  return `${b64}.${sig}`;
}

/**
 * Calls the worker's fetch handler directly (bypassing SELF service binding)
 * so we can flush ctx.waitUntil() tasks before assertions.
 * Required for /file/ tests because the route calls ctx.waitUntil(cache.put(...)).
 */
async function workerFetch(url: string, init?: RequestInit): Promise<Response> {
  const ctx = createExecutionContext();
  const res = await worker.fetch(new Request(url, init), env as any, ctx);
  await waitOnExecutionContext(ctx);
  return res;
}

// Each test uses a unique R2 key so no cross-test state leaks.
// reset() is intentionally omitted: it races with Miniflare's internal ZIP
// response buffering and produces spurious "Unspecified error (0)" rejections.

// ---------------------------------------------------------------------------
// Routing
// ---------------------------------------------------------------------------
describe("routing", () => {
  it("returns 404 for unknown paths", async () => {
    const res = await SELF.fetch("http://worker/unknown");
    expect(res.status).toBe(404);
  });

  it("returns CORS headers for OPTIONS preflight", async () => {
    const res = await SELF.fetch("http://worker/file/k?token=x", { method: "OPTIONS" });
    expect(res.status).toBe(200);
    expect(res.headers.get("Access-Control-Allow-Origin")).toBe("*");
    expect(res.headers.get("Access-Control-Allow-Methods")).toBe("GET");
  });

  it("returns 405 for non-GET requests", async () => {
    const res = await SELF.fetch("http://worker/file/k?token=x", { method: "POST" });
    expect(res.status).toBe(405);
  });
});

// ---------------------------------------------------------------------------
// /branding/ — public, no token required
// ---------------------------------------------------------------------------
describe("/branding/", () => {
  it("serves the asset with public Cache-Control and CORS", async () => {
    await env.BUCKET.put("branding/logo.txt", "fake-logo-data", {
      httpMetadata: { contentType: "text/plain" },
    });

    const res = await SELF.fetch("http://worker/branding/logo.txt");

    expect(res.status).toBe(200);
    expect(res.headers.get("Cache-Control")).toBe("public, max-age=86400");
    expect(res.headers.get("Access-Control-Allow-Origin")).toBe("*");
    expect(await res.text()).toBe("fake-logo-data");
  });

  it("returns 404 for a missing branding asset", async () => {
    const res = await SELF.fetch("http://worker/branding/missing.png");
    expect(res.status).toBe(404);
  });
});

// ---------------------------------------------------------------------------
// /file/ — single file, HMAC-token authenticated, edge-cached
//
// Each test uses a unique r2Key so that cache entries from one test cannot
// bleed into another (the route caches canonical bytes by a versioned path key).
// Tests use workerFetch() (direct handler) so ctx.waitUntil(cache.put(...))
// is flushed before reset() is called in afterEach.
// ---------------------------------------------------------------------------
describe("/file/", () => {
  it("returns 401 when no token is provided", async () => {
    const res = await SELF.fetch("http://worker/file/some-key");
    expect(res.status).toBe(401);
  });

  it("returns 401 for an invalid token signature", async () => {
    const res = await SELF.fetch("http://worker/file/k?token=eyJleHAiOjk5OTk5OTk5OTl9.badsig");
    expect(res.status).toBe(401);
  });

  it("returns 401 for an expired token", async () => {
    const token = await signToken({ r2_key: "some-key" }, -10);
    const res = await SELF.fetch(`http://worker/file/some-key?token=${token}`);
    expect(res.status).toBe(401);
  });

  it("returns 400 when token payload has no r2_key", async () => {
    const token = await signToken({});
    const res = await SELF.fetch(`http://worker/file/some-key?token=${token}`);
    expect(res.status).toBe(400);
  });

  it("returns 404 when the R2 object does not exist", async () => {
    const token = await signToken({ r2_key: "ghost-key" });
    const res = await workerFetch(`http://worker/file/ghost-key?token=${token}`);
    expect(res.status).toBe(404);
  });

  it("serves the file with long-lived Cache-Control and CORS", async () => {
    const r2Key = "files/serve-test.txt";
    await env.BUCKET.put(r2Key, "hello world", { httpMetadata: { contentType: "text/plain" } });
    const token = await signToken({ r2_key: r2Key });

    const res = await workerFetch(`http://worker/file/${r2Key}?token=${token}`);

    expect(res.status).toBe(200);
    expect(res.headers.get("Cache-Control")).toBe("private, no-store");
    expect(res.headers.get("Access-Control-Allow-Origin")).toBe("*");
    expect(await res.text()).toBe("hello world");
  });

  it("binds a capability to the decoded file path before consulting cache", async () => {
    const allowedKey = "files/cache-binding-allowed.txt";
    const protectedKey = "files/cache-binding-protected.txt";
    await env.BUCKET.put(allowedKey, "allowed");
    await env.BUCKET.put(protectedKey, "protected");

    const protectedToken = await signToken({ r2_key: protectedKey });
    const primed = await workerFetch(
      `http://worker/file/${protectedKey}?token=${protectedToken}`,
    );
    expect(await primed.text()).toBe("protected");

    const allowedToken = await signToken({ r2_key: allowedKey });
    const attack = await workerFetch(
      `http://worker/file/${protectedKey}?token=${allowedToken}`,
    );
    expect(attack.status).toBe(403);
    expect(await attack.text()).not.toContain("protected");
  });

  it("applies response variants after a shared body-cache hit", async () => {
    const r2Key = "files/cache-header-variant.pdf";
    await env.BUCKET.put(r2Key, "pdf-bytes", {
      httpMetadata: { contentType: "application/pdf" },
    });

    const firstToken = await signToken({ r2_key: r2Key, filename: "First.pdf" });
    const first = await workerFetch(`http://worker/file/${r2Key}?token=${firstToken}`);
    expect(first.headers.get("Content-Disposition")).toContain('filename="First.pdf"');
    await first.arrayBuffer();

    const secondToken = await signToken({
      r2_key: r2Key,
      filename: 'Second "safe".pdf',
      force_download: true,
      content_type: "application/octet-stream",
    });
    const second = await workerFetch(`http://worker/file/${r2Key}?token=${secondToken}`);
    expect(second.headers.get("Content-Disposition")).toContain("attachment");
    expect(second.headers.get("Content-Disposition")).toContain(
      'filename="Second _safe_.pdf"',
    );
    expect(second.headers.get("Content-Type")).toBe("application/octet-stream");
    expect(await second.text()).toBe("pdf-bytes");
  });

  it("sets inline Content-Disposition by default", async () => {
    const r2Key = "files/inline-test.pdf";
    await env.BUCKET.put(r2Key, "pdf-bytes", { httpMetadata: { contentType: "application/pdf" } });
    const token = await signToken({ r2_key: r2Key });

    const res = await workerFetch(`http://worker/file/${r2Key}?token=${token}`);

    expect(res.headers.get("Content-Disposition")).toBe("inline");
  });

  it("sets attachment Content-Disposition with force_download", async () => {
    const r2Key = "files/force-download-test.pdf";
    await env.BUCKET.put(r2Key, "pdf-bytes", { httpMetadata: { contentType: "application/pdf" } });
    const token = await signToken({ r2_key: r2Key, force_download: true });

    const res = await workerFetch(`http://worker/file/${r2Key}?token=${token}`);

    expect(res.headers.get("Content-Disposition")).toBe("attachment");
  });

  it("sets RFC 5987 Content-Disposition when a filename is provided", async () => {
    const r2Key = "files/filename-test.pdf";
    await env.BUCKET.put(r2Key, "pdf-bytes", { httpMetadata: { contentType: "application/pdf" } });
    // Non-ASCII (é) → _ in ASCII fallback; encoded as %C3%A9 in filename*
    const token = await signToken({ r2_key: r2Key, filename: "Cours Réseaux.pdf" });

    const res = await workerFetch(`http://worker/file/${r2Key}?token=${token}`);
    const disposition = res.headers.get("Content-Disposition") ?? "";

    expect(disposition).toContain("inline");
    expect(disposition).toContain('filename="Cours R_seaux.pdf"');
    expect(disposition).toContain("filename*=UTF-8''Cours%20R%C3%A9seaux.pdf");
  });

  it("overrides Content-Type from the token payload", async () => {
    const r2Key = "files/content-type-test.pdf";
    await env.BUCKET.put(r2Key, "pdf-bytes", { httpMetadata: { contentType: "application/pdf" } });
    const token = await signToken({ r2_key: r2Key, content_type: "application/octet-stream" });

    const res = await workerFetch(`http://worker/file/${r2Key}?token=${token}`);

    expect(res.headers.get("Content-Type")).toBe("application/octet-stream");
  });

  it("decompresses gzip content and removes content-encoding header", async () => {
    const r2Key = "files/gzip-test.txt";
    const original = "hello from compressed file";
    const cs = new CompressionStream("gzip");
    const writer = cs.writable.getWriter();
    await writer.write(new TextEncoder().encode(original));
    await writer.close();
    const compressed = await new Response(cs.readable).arrayBuffer();

    await env.BUCKET.put(r2Key, compressed, {
      httpMetadata: { contentType: "text/plain", contentEncoding: "gzip" },
    });
    const token = await signToken({ r2_key: r2Key });

    const res = await workerFetch(`http://worker/file/${r2Key}?token=${token}`);

    expect(res.status).toBe(200);
    expect(res.headers.get("content-encoding")).toBeNull();
    expect(res.headers.get("content-length")).toBeNull();
    expect(res.headers.get("Accept-Ranges")).toBe("none");
    expect(await res.text()).toBe(original);
  });

  it("passes a byte range through R2 and returns a partial response", async () => {
    const r2Key = "files/range-video.bin";
    await env.BUCKET.put(r2Key, "0123456789", {
      httpMetadata: { contentType: "video/mp4" },
    });
    const token = await signToken({ r2_key: r2Key, content_type: "video/mp4" });

    const res = await workerFetch(`http://worker/file/${r2Key}?token=${token}`, {
      headers: { Range: "bytes=2-5" },
    });

    expect(res.status).toBe(206);
    expect(res.headers.get("Accept-Ranges")).toBe("bytes");
    expect(res.headers.get("Content-Range")).toBe("bytes 2-5/10");
    expect(res.headers.get("Content-Length")).toBe("4");
    expect(await res.text()).toBe("2345");
  });

  it("returns 416 with the object size for an unsatisfiable R2 range", async () => {
    const r2Key = "files/range-too-far.bin";
    await env.BUCKET.put(r2Key, "0123456789");
    const token = await signToken({ r2_key: r2Key });

    const res = await workerFetch(`http://worker/file/${r2Key}?token=${token}`, {
      headers: { Range: "bytes=50-60" },
    });

    expect(res.status).toBe(416);
    expect(res.headers.get("Content-Range")).toBe("bytes */10");
  });
});

// ---------------------------------------------------------------------------
// /zip — streaming ZIP archive
// ---------------------------------------------------------------------------
describe("/zip", () => {
  it("returns 401 when no token is provided", async () => {
    const res = await SELF.fetch("http://worker/zip");
    expect(res.status).toBe(401);
  });

  it("returns 401 for an invalid token", async () => {
    const res = await SELF.fetch("http://worker/zip?token=eyJleHAiOjF9.badsig");
    expect(res.status).toBe(401);
  });

  it("returns 400 when entries is missing from the token payload", async () => {
    const token = await signToken({ dir_name: "Cours" });
    const res = await SELF.fetch(`http://worker/zip?token=${token}`);
    expect(res.status).toBe(400);
  });

  it("returns 400 when dir_name is missing from the token payload", async () => {
    const token = await signToken({ entries: [{ arcname: "f.txt", r2_key: "k" }] });
    const res = await SELF.fetch(`http://worker/zip?token=${token}`);
    expect(res.status).toBe(400);
  });

  it("streams a non-empty ZIP with correct headers", async () => {
    await env.BUCKET.put("zip/a.txt", "content A", { httpMetadata: { contentType: "text/plain" } });
    await env.BUCKET.put("zip/b.txt", "content B", { httpMetadata: { contentType: "text/plain" } });
    const token = await signToken({
      dir_name: "Mon Cours",
      entries: [
        { arcname: "a.txt", r2_key: "zip/a.txt" },
        { arcname: "b.txt", r2_key: "zip/b.txt" },
      ],
    });

    const res = await SELF.fetch(`http://worker/zip?token=${token}`);

    expect(res.status).toBe(200);
    expect(res.headers.get("Content-Type")).toBe("application/zip");
    expect(res.headers.get("Access-Control-Allow-Origin")).toBe("*");
    const disposition = res.headers.get("Content-Disposition") ?? "";
    expect(disposition).toContain("attachment");
    expect(disposition).toContain('"Mon Cours.zip"');
    expect(disposition).toContain("filename*=UTF-8''Mon%20Cours.zip");
    const body = await res.arrayBuffer();
    expect(body.byteLength).toBeGreaterThan(0);
  });

  it("URL-encodes non-ASCII characters in the ZIP filename", async () => {
    await env.BUCKET.put("zip/a.txt", "content A", { httpMetadata: { contentType: "text/plain" } });
    const token = await signToken({
      dir_name: "Cours Réseaux",
      entries: [{ arcname: "a.txt", r2_key: "zip/a.txt" }],
    });

    const res = await SELF.fetch(`http://worker/zip?token=${token}`);
    const disposition = res.headers.get("Content-Disposition") ?? "";

    expect(disposition).toContain("filename*=UTF-8''");
    expect(disposition).toContain("Cours%20R%C3%A9seaux.zip");
  });

  it("appends a part suffix for parts beyond the first", async () => {
    await env.BUCKET.put("zip/a.txt", "content A", { httpMetadata: { contentType: "text/plain" } });
    const token = await signToken({
      dir_name: "Cours",
      entries: [{ arcname: "a.txt", r2_key: "zip/a.txt" }],
      part: 2,
      total: 3,
    });

    const res = await SELF.fetch(`http://worker/zip?token=${token}`);
    expect(res.headers.get("Content-Disposition")).toContain("(2)");
  });

  it("omits the part suffix for part 1", async () => {
    await env.BUCKET.put("zip/a.txt", "content A", { httpMetadata: { contentType: "text/plain" } });
    const token = await signToken({
      dir_name: "Cours",
      entries: [{ arcname: "a.txt", r2_key: "zip/a.txt" }],
      part: 1,
      total: 3,
    });

    const res = await SELF.fetch(`http://worker/zip?token=${token}`);
    expect(res.headers.get("Content-Disposition")).not.toContain("(1)");
  });

  it("skips missing R2 keys and still produces a valid ZIP", async () => {
    await env.BUCKET.put("zip/a.txt", "content A", { httpMetadata: { contentType: "text/plain" } });
    const token = await signToken({
      dir_name: "Cours",
      entries: [
        { arcname: "a.txt", r2_key: "zip/a.txt" },
        { arcname: "missing.txt", r2_key: "zip/nonexistent.txt" },
      ],
    });

    const res = await SELF.fetch(`http://worker/zip?token=${token}`);

    expect(res.status).toBe(200);
    expect(res.headers.get("Content-Type")).toBe("application/zip");
    const body = await res.arrayBuffer();
    expect(body.byteLength).toBeGreaterThan(0);
  });
});
