import { downloadZip } from "client-zip";

export interface Env {
  BUCKET: R2Bucket;
  HMAC_SECRET: string;
}

interface ZipEntry {
  arcname: string;
  r2_key: string;
}

interface ZipPayload {
  dir_name: string;
  entries: ZipEntry[];
  exp: number;
  part?: number;
  total?: number;
}

function b64urlDecode(s: string): ArrayBuffer {
  const b64 = s.replace(/-/g, "+").replace(/_/g, "/");
  const padded = b64 + "==".slice(0, (4 - (b64.length % 4)) % 4);
  const raw = atob(padded);
  const buf = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) buf[i] = raw.charCodeAt(i);
  return buf.buffer;
}

async function verifyToken(
  token: string,
  secret: string
): Promise<ZipPayload | null> {
  const dot = token.lastIndexOf(".");
  if (dot === -1) return null;

  const payloadPart = token.slice(0, dot);
  const sigPart = token.slice(dot + 1);

  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["verify"]
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
    new TextEncoder().encode(payloadPart)
  );
  if (!valid) return null;

  let payload: ZipPayload;
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

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname !== "/zip") {
      return new Response("Not found", { status: 404 });
    }

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

    const token = url.searchParams.get("token");
    if (!token) {
      return new Response("Missing token", { status: 401 });
    }

    const payload = await verifyToken(token, env.HMAC_SECRET);
    if (!payload) {
      return new Response("Invalid or expired token", { status: 401 });
    }

    // Async generator so R2 fetches happen one at a time (lazy) but each yielded
    // item has a concrete ReadableStream — client-zip does not accept functions.
    async function* streamFiles() {
      for (const { arcname, r2_key } of payload!.entries) {
        const obj = await env.BUCKET.get(r2_key);
        if (!obj) {
          yield { name: arcname, input: new Response(""), size: 0 };
        } else {
          // Passing size lets client-zip write the correct local file header
          // upfront, avoiding data descriptors and improving unzipper compatibility.
          yield { name: arcname, input: obj.body, size: obj.size };
        }
      }
    }
    const files = streamFiles();

    const suffix =
      payload.part && payload.total && payload.total > 1 && payload.part > 1
        ? ` (${payload.part})`
        : "";
    const baseName =
      payload.dir_name.replace(/[^\x20-\x7E]/g, "_").replace(/\//g, "_") ||
      "directory";
    const asciiFallback = baseName + suffix;
    const encodedName = encodeURIComponent(payload.dir_name + suffix);
    const disposition = `attachment; filename="${asciiFallback}.zip"; filename*=UTF-8''${encodedName}.zip`;

    const zipResponse = downloadZip(files);

    return new Response(zipResponse.body, {
      headers: {
        "Content-Type": "application/zip",
        "Content-Disposition": disposition,
      },
    });
  },
};
