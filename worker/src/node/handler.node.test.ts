/**
 * Node-runtime tests for the shared delivery handler.
 *
 * These run on plain Node (not workerd) with a fake in-memory ObjectSource,
 * proving the self-hosted path (server.ts) exercises the same logic the
 * Cloudflare Worker does — token verification, branding, single-file serving,
 * gzip decompression and ZIP streaming — using Node's global Web APIs.
 */

import { createHmac } from "node:crypto";

import { describe, expect, it } from "vitest";

import { handleRequest, ObjectSource, StoredObject } from "../handler.js";

const SECRET = "node-test-secret";

function signToken(payload: Record<string, unknown>, expOffsetSeconds = 3600): string {
  const full = { ...payload, exp: Math.floor(Date.now() / 1000) + expOffsetSeconds };
  const b64 = (b: Buffer) =>
    b.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=/g, "");
  const payloadB64 = b64(Buffer.from(JSON.stringify(full)));
  const sig = createHmac("sha256", SECRET).update(payloadB64).digest();
  return `${payloadB64}.${b64(sig)}`;
}

interface FakeObject {
  bytes: Uint8Array;
  contentType?: string;
  contentEncoding?: string;
}

class FakeSource implements ObjectSource {
  constructor(private readonly objects: Map<string, FakeObject>) {}

  async get(key: string): Promise<StoredObject | null> {
    const obj = this.objects.get(key);
    if (!obj) return null;
    return {
      body: new Response(obj.bytes).body!,
      size: obj.bytes.byteLength,
      contentEncoding: obj.contentEncoding,
      etag: '"fake-etag"',
      writeHttpMetadata(headers: Headers) {
        if (obj.contentType) headers.set("Content-Type", obj.contentType);
        if (obj.contentEncoding) headers.set("Content-Encoding", obj.contentEncoding);
      },
    };
  }
}

function source(entries: Record<string, FakeObject>): ObjectSource {
  return new FakeSource(new Map(Object.entries(entries)));
}

async function gzip(text: string): Promise<Uint8Array> {
  const cs = new CompressionStream("gzip");
  const writer = cs.writable.getWriter();
  await writer.write(new TextEncoder().encode(text));
  await writer.close();
  return new Uint8Array(await new Response(cs.readable).arrayBuffer());
}

function req(path: string, init?: RequestInit): Request {
  return new Request(`http://worker${path}`, init);
}

const utf8 = (s: string) => new TextEncoder().encode(s);

describe("self-hosted handler — routing", () => {
  it("404s unknown paths", async () => {
    const res = await handleRequest(req("/unknown"), { source: source({}), secret: SECRET });
    expect(res.status).toBe(404);
  });

  it("answers OPTIONS preflight with CORS", async () => {
    const res = await handleRequest(req("/file/k?token=x", { method: "OPTIONS" }), {
      source: source({}),
      secret: SECRET,
    });
    expect(res.status).toBe(200);
    expect(res.headers.get("Access-Control-Allow-Methods")).toBe("GET");
  });

  it("405s non-GET", async () => {
    const res = await handleRequest(req("/file/k?token=x", { method: "POST" }), {
      source: source({}),
      secret: SECRET,
    });
    expect(res.status).toBe(405);
  });
});

describe("self-hosted handler — /branding", () => {
  it("serves public assets without a token", async () => {
    const res = await handleRequest(req("/branding/logo.txt"), {
      source: source({ "branding/logo.txt": { bytes: utf8("logo"), contentType: "text/plain" } }),
      secret: SECRET,
    });
    expect(res.status).toBe(200);
    expect(res.headers.get("Cache-Control")).toBe("public, max-age=86400");
    expect(await res.text()).toBe("logo");
  });
});

describe("self-hosted handler — /file", () => {
  it("401s without a token", async () => {
    const res = await handleRequest(req("/file/k"), { source: source({}), secret: SECRET });
    expect(res.status).toBe(401);
  });

  it("401s an expired token", async () => {
    const token = signToken({ r2_key: "k" }, -10);
    const res = await handleRequest(req(`/file/k?token=${token}`), {
      source: source({ k: { bytes: utf8("x") } }),
      secret: SECRET,
    });
    expect(res.status).toBe(401);
  });

  it("404s a missing object", async () => {
    const token = signToken({ r2_key: "ghost" });
    const res = await handleRequest(req(`/file/ghost?token=${token}`), {
      source: source({}),
      secret: SECRET,
    });
    expect(res.status).toBe(404);
  });

  it("serves with 1-month cache and inline disposition", async () => {
    const token = signToken({ r2_key: "files/a.txt" });
    const res = await handleRequest(req(`/file/files/a.txt?token=${token}`), {
      source: source({ "files/a.txt": { bytes: utf8("hello"), contentType: "text/plain" } }),
      secret: SECRET,
    });
    expect(res.status).toBe(200);
    expect(res.headers.get("Cache-Control")).toBe("public, max-age=2592000");
    expect(res.headers.get("Content-Disposition")).toBe("inline");
    expect(await res.text()).toBe("hello");
  });

  it("sets RFC 5987 disposition for non-ASCII filenames", async () => {
    const token = signToken({ r2_key: "k", filename: "Cours Réseaux.pdf" });
    const res = await handleRequest(req(`/file/k?token=${token}`), {
      source: source({ k: { bytes: utf8("x"), contentType: "application/pdf" } }),
      secret: SECRET,
    });
    const disposition = res.headers.get("Content-Disposition") ?? "";
    expect(disposition).toContain('filename="Cours R_seaux.pdf"');
    expect(disposition).toContain("filename*=UTF-8''Cours%20R%C3%A9seaux.pdf");
  });

  it("decompresses gzip and drops content-encoding", async () => {
    const original = "compressed payload";
    const token = signToken({ r2_key: "g" });
    const res = await handleRequest(req(`/file/g?token=${token}`), {
      source: source({
        g: { bytes: await gzip(original), contentType: "text/plain", contentEncoding: "gzip" },
      }),
      secret: SECRET,
    });
    expect(res.status).toBe(200);
    expect(res.headers.get("content-encoding")).toBeNull();
    expect(await res.text()).toBe(original);
  });
});

describe("self-hosted handler — /zip", () => {
  it("streams a ZIP with the right headers", async () => {
    const token = signToken({
      dir_name: "Mon Cours",
      entries: [
        { arcname: "a.txt", r2_key: "z/a.txt" },
        { arcname: "b.txt", r2_key: "z/b.txt" },
      ],
    });
    const res = await handleRequest(req(`/zip?token=${token}`), {
      source: source({
        "z/a.txt": { bytes: utf8("A") },
        "z/b.txt": { bytes: utf8("B") },
      }),
      secret: SECRET,
    });
    expect(res.status).toBe(200);
    expect(res.headers.get("Content-Type")).toBe("application/zip");
    const disposition = res.headers.get("Content-Disposition") ?? "";
    expect(disposition).toContain('"Mon Cours.zip"');
    expect(disposition).toContain("filename*=UTF-8''Mon%20Cours.zip");
    expect((await res.arrayBuffer()).byteLength).toBeGreaterThan(0);
  });

  it("skips missing keys and still produces a ZIP", async () => {
    const token = signToken({
      dir_name: "Cours",
      entries: [
        { arcname: "a.txt", r2_key: "z/a.txt" },
        { arcname: "missing.txt", r2_key: "z/nope.txt" },
      ],
    });
    const res = await handleRequest(req(`/zip?token=${token}`), {
      source: source({ "z/a.txt": { bytes: utf8("A") } }),
      secret: SECRET,
    });
    expect(res.status).toBe(200);
    expect((await res.arrayBuffer()).byteLength).toBeGreaterThan(0);
  });
});
