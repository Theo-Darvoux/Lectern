/**
 * Self-hosted worker — Node entry point.
 *
 * The off-Cloudflare equivalent of src/index.ts. Same {@link handleRequest}
 * logic, but the object source is an S3 client (SeaweedFS/Garage/RustFS) and
 * there is no in-process edge cache. Public branding may be cached by nginx,
 * but authenticated file/ZIP requests must reach this handler so the HMAC is
 * verified on every request (see infra/nginx/worker-cache.conf). It honours the same WORKER_ZIP_HMAC_SECRET token
 * contract as the API, so the API only needs WORKER_ZIP_URL pointed here.
 *
 * Config (env):
 *   PORT                    listen port (default 8788)
 *   WORKER_ZIP_HMAC_SECRET  shared HMAC secret (matches the API)
 *   S3_ENDPOINT             host:port of the S3 store (e.g. seaweedfs:8333)
 *   S3_USE_SSL              "true" → https, else http
 *   S3_REGION               S3 region (default us-east-1)
 *   S3_ACCESS_KEY / S3_SECRET_KEY
 *   S3_BUCKET               bucket name (default lectern)
 */

import http from "node:http";
import { Readable } from "node:stream";

import { S3Client } from "@aws-sdk/client-s3";

import { handleRequest } from "../handler.js";
import { s3Source } from "./s3-source.js";

const PORT = Number(process.env.PORT ?? 8788);
const SECRET = process.env.WORKER_ZIP_HMAC_SECRET ?? process.env.HMAC_SECRET ?? "";
const BUCKET = process.env.S3_BUCKET ?? "lectern";
const REGION = process.env.S3_REGION ?? "us-east-1";
const USE_SSL = (process.env.S3_USE_SSL ?? "false").toLowerCase() === "true";
const ENDPOINT_HOST = process.env.S3_ENDPOINT ?? "localhost:8333";

if (Buffer.byteLength(SECRET, "utf8") < 32) {
  throw new Error("WORKER_ZIP_HMAC_SECRET must contain at least 32 bytes");
}

const client = new S3Client({
  endpoint: `${USE_SSL ? "https" : "http"}://${ENDPOINT_HOST}`,
  region: REGION,
  credentials: {
    accessKeyId: process.env.S3_ACCESS_KEY ?? "",
    secretAccessKey: process.env.S3_SECRET_KEY ?? "",
  },
  // Path-style is required by SeaweedFS/MinIO-style stores (no vhost buckets).
  forcePathStyle: true,
});

const source = s3Source(client, BUCKET);

function toHeaders(raw: http.IncomingHttpHeaders): Headers {
  const headers = new Headers();
  for (const [key, value] of Object.entries(raw)) {
    if (value === undefined) continue;
    if (Array.isArray(value)) {
      for (const v of value) headers.append(key, v);
    } else {
      headers.set(key, value);
    }
  }
  return headers;
}

const server = http.createServer(async (req, res) => {
  // Lightweight health endpoint for container/nginx checks.
  if (req.url === "/healthz") {
    res.statusCode = 200;
    res.end("ok");
    return;
  }

  try {
    const url = `http://${req.headers.host ?? "localhost"}${req.url ?? "/"}`;
    const request = new Request(url, {
      method: req.method,
      headers: toHeaders(req.headers),
    });

    const response = await handleRequest(request, { source, secret: SECRET });

    res.statusCode = response.status;
    response.headers.forEach((value, key) => res.setHeader(key, value));

    if (response.body) {
      Readable.fromWeb(response.body as Parameters<typeof Readable.fromWeb>[0]).pipe(res);
    } else {
      res.end();
    }
  } catch (err) {
    console.error("[selfhost-worker] request failed:", err);
    if (!res.headersSent) res.statusCode = 500;
    res.end("Internal error");
  }
});

server.listen(PORT, () => {
  console.log(`[selfhost-worker] listening on :${PORT} → ${ENDPOINT_HOST}/${BUCKET}`);
});
