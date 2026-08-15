/**
 * S3-backed {@link ObjectSource} for the self-hosted Node deployment.
 *
 * Works against any S3-compatible store (SeaweedFS, Garage, RustFS) — switching
 * backends is purely an endpoint/credentials change, never a code change. The
 * SDK's path-style addressing (forcePathStyle) keeps it compatible with stores
 * that don't do virtual-host buckets.
 */

import { GetObjectCommand, S3Client } from "@aws-sdk/client-s3";

import { RangeNotSatisfiableError } from "../handler.js";
import type { ObjectSource, StoredObject } from "../handler.js";

export function s3Source(client: S3Client, bucket: string): ObjectSource {
  return {
    async get(key: string, rangeHeader?: string): Promise<StoredObject | null> {
      let resp;
      try {
        resp = await client.send(
          new GetObjectCommand({ Bucket: bucket, Key: key, Range: rangeHeader }),
        );
      } catch (err: unknown) {
        if (isNotFound(err)) return null;
        if (isInvalidRange(err)) throw new RangeNotSatisfiableError();
        throw err;
      }
      if (!resp.Body) return null;

      // aws-sdk v3 stream mixin: turn the body into a Web ReadableStream so it
      // flows straight into the Web `Response` the handler builds.
      const body = (
        resp.Body as { transformToWebStream(): ReadableStream }
      ).transformToWebStream();

      return {
        body,
        size: resp.ContentLength,
        contentEncoding: resp.ContentEncoding,
        etag: resp.ETag,
        contentRange: resp.ContentRange,
        writeHttpMetadata(headers: Headers) {
          if (resp.ContentType) headers.set("Content-Type", resp.ContentType);
          if (resp.ContentEncoding) headers.set("Content-Encoding", resp.ContentEncoding);
          if (resp.ContentDisposition)
            headers.set("Content-Disposition", resp.ContentDisposition);
          if (resp.CacheControl) headers.set("Cache-Control", resp.CacheControl);
          if (resp.ContentLanguage) headers.set("Content-Language", resp.ContentLanguage);
        },
      };
    },
  };
}

function isInvalidRange(err: unknown): boolean {
  const e = err as { name?: string; Code?: string; $metadata?: { httpStatusCode?: number } };
  return (
    e?.name === "InvalidRange" ||
    e?.Code === "InvalidRange" ||
    e?.$metadata?.httpStatusCode === 416
  );
}

function isNotFound(err: unknown): boolean {
  const e = err as { name?: string; Code?: string; $metadata?: { httpStatusCode?: number } };
  return (
    e?.name === "NoSuchKey" ||
    e?.name === "NotFound" ||
    e?.Code === "NoSuchKey" ||
    e?.$metadata?.httpStatusCode === 404
  );
}
