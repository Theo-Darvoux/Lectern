# ADR 005 — Browser upload transport and queue

## Status

Accepted

## Context

The browser could send a file four ways (direct POST, presigned PUT, tus, presigned
multipart) and picked by size. The largest files used the only path without resume,
each `UploadDrawer` kept its own `File` references for one shared queue, and a transfer
slot stayed occupied while the server scanned the file.

## Decision

1. Files below `DIRECT_UPLOAD_THRESHOLD_MB` go through `POST /api/upload`. Larger files
   use presigned multipart, which sends parts straight to object storage in parallel.
   tus remains the fallback: for deployments that set `ENABLE_PRESIGNED_MULTIPART=false`,
   and automatically when a browser cannot use the bucket (blocked CORS or unreadable `ETag`).
2. Multipart sessions last 24 hours and are resumable: the server lists stored parts
   and signs fresh URLs for the missing ones.
3. One module, `web/src/lib/upload-runtime.ts`, owns `File` references and the transfer
   queue for the whole page. Drawers are views over it, so uploads continue when a
   drawer closes or the user navigates.
4. Four files transfer at once, and a file releases its slot when its bytes are stored.
5. Each queue row stores its destination and is kept per tab in `sessionStorage`.

## Consequences

- Direct-to-bucket speed needs CORS exposing `ETag`. Without it, uploads still work
  through the API, after one failed probe per page.
- `/api/upload/init` and `/api/upload/batch-zip` remain for existing API clients only.
- A reload cannot recover `File` objects: stored files keep processing automatically,
  and unfinished transfers need the file re-attached or the folder dropped again.
