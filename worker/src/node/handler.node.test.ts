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

import {
  handleRequest,
  ObjectSource,
  RangeNotSatisfiableError,
  StoredObject,
} from "../handler.js";

const SECRET = "node-test-secret-at-least-32-bytes";

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
  readonly requestedRanges: Array<string | undefined> = [];

  constructor(private readonly objects: Map<string, FakeObject>) {}

  async get(key: string, rangeHeader?: string): Promise<StoredObject | null> {
    this.requestedRanges.push(rangeHeader);
    const obj = this.objects.get(key);
    if (!obj) return null;
    let bytes = obj.bytes;
    let contentRange: string | undefined;
    if (rangeHeader) {
      const match = /^bytes=(\d+)-(\d+)$/.exec(rangeHeader);
      if (!match) throw new Error(`unsupported fake range: ${rangeHeader}`);
      const start = Number(match[1]);
      const end = Math.min(Number(match[2]), bytes.byteLength - 1);
      bytes = bytes.slice(start, end + 1);
      contentRange = `bytes ${start}-${end}/${obj.bytes.byteLength}`;
    }
    return {
      body: new Response(bytes).body!,
      size: bytes.byteLength,
      contentEncoding: obj.contentEncoding,
      etag: '"fake-etag"',
      contentRange,
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
    expect(res.headers.get("Cache-Control")).toBe("private, no-store");
    expect(res.headers.get("Content-Disposition")).toBe("inline");
    expect(await res.text()).toBe("hello");
  });

  it("rejects a valid token used with a different URL path", async () => {
    const token = signToken({ r2_key: "allowed" });
    const res = await handleRequest(req(`/file/protected?token=${token}`), {
      source: source({
        allowed: { bytes: utf8("allowed") },
        protected: { bytes: utf8("protected") },
      }),
      secret: SECRET,
    });
    expect(res.status).toBe(403);
    expect(await res.text()).not.toContain("protected");
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

  it("serves a requested byte range without fetching the full representation", async () => {
    const token = signToken({ r2_key: "media" });
    const fake = new FakeSource(
      new Map([["media", { bytes: utf8("0123456789"), contentType: "video/mp4" }]]),
    );
    const res = await handleRequest(
      req(`/file/media?token=${token}`, { headers: { Range: "bytes=2-5" } }),
      { source: fake, secret: SECRET },
    );

    expect(res.status).toBe(206);
    expect(res.headers.get("Accept-Ranges")).toBe("bytes");
    expect(res.headers.get("Content-Range")).toBe("bytes 2-5/10");
    expect(res.headers.get("Content-Length")).toBe("4");
    expect(await res.text()).toBe("2345");
    expect(fake.requestedRanges).toEqual(["bytes=2-5"]);
  });

  it("rejects multiple ranges before accessing storage", async () => {
    const token = signToken({ r2_key: "media" });
    const fake = new FakeSource(new Map([["media", { bytes: utf8("0123456789") }]]));
    const res = await handleRequest(
      req(`/file/media?token=${token}`, { headers: { Range: "bytes=0-1,4-5" } }),
      { source: fake, secret: SECRET },
    );

    expect(res.status).toBe(416);
    expect(fake.requestedRanges).toEqual([]);
  });

  it("maps an unsatisfiable storage range to 416", async () => {
    const token = signToken({ r2_key: "media" });
    const rangeSource: ObjectSource = {
      async get() {
        throw new RangeNotSatisfiableError(10);
      },
    };
    const res = await handleRequest(
      req(`/file/media?token=${token}`, { headers: { Range: "bytes=50-60" } }),
      { source: rangeSource, secret: SECRET },
    );

    expect(res.status).toBe(416);
    expect(res.headers.get("Content-Range")).toBe("bytes */10");
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
    expect(res.headers.get("Accept-Ranges")).toBe("none");
    expect(await res.text()).toBe(original);
  });

  it("does not expose compressed-byte ranges as decompressed ranges", async () => {
    const original = "compressed representation";
    const token = signToken({ r2_key: "g" });
    const fake = new FakeSource(
      new Map([["g", { bytes: await gzip(original), contentEncoding: "gzip" }]]),
    );
    const res = await handleRequest(
      req(`/file/g?token=${token}`, { headers: { Range: "bytes=0-4" } }),
      { source: fake, secret: SECRET },
    );

    expect(res.status).toBe(200);
    expect(res.headers.get("Content-Range")).toBeNull();
    expect(res.headers.get("Accept-Ranges")).toBe("none");
    expect(await res.text()).toBe(original);
    expect(fake.requestedRanges).toEqual(["bytes=0-4", undefined]);
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
