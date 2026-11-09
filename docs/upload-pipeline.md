# Upload Pipeline

Every file upload goes through two phases: an **admission and transfer gateway**
in the API followed by hostile-file scanning and processing in an ARQ worker.

---

## Overview

```
Client                       API                          ARQ Worker
  │                           │                               │
  ├─ validate (size/type) ───►│                               │
  ├─ transfer to S3 ─────────►│ quarantine/{user}/{id}/file   │
  ├─ complete upload ────────►│                               │
  │                           ├── enqueue ───────────────────►│
  │◄── upload_id, status ─────┤                               │
  │                           │                               ├─ download & hash    
  │                           │                               ├─ YARA scan
  │                           │                               ├─ strip metadata
  │                           │                               ├─ publish to CAS
  │                           │                               ├─ generate thumbnail
  │◄── SSE: processing_status ┤◄── SSE event ─────────────────┤
```

---

## Phase 1 — Prescan gateway (API)

### 1. Initiation

Every entry point validates:
- File size against per-type and global limits
- MIME type and extension against the allowlist (including `ALLOWED_EXTENSIONS` and `ALLOWED_MIME_TYPES` when set)
- Storage capacity
- Pending upload cap (prevents queue flooding)

A database `Upload` row is created with `status=pending` and a `quarantine_key` pointing to `quarantine/{user_id}/{upload_id}/{filename}` in S3.

### 2. Transfer

The client chooses a transfer strategy based on `GET /api/upload/config`:

| Size | Strategy |
|---|---|
| Below `DIRECT_UPLOAD_THRESHOLD_MB` (10 MiB by default) | `POST /api/upload` through the API |
| At or above that threshold | Presigned multipart straight to S3 (8 MiB parts, 4 in parallel) |
| Multipart disabled, or the browser cannot reach the bucket | Resumable [tus](https://tus.io/) through the API (8 MiB chunks) |

Four files transfer at once. A file releases its slot as soon as its bytes are
stored, so server-side scanning never delays the next transfer.

Transfers survive interruptions:

- Direct uploads retry transient failures with the same `X-Upload-ID`. The server
  returns the original upload rather than creating a second one.
- Multipart sessions last 24 hours. `POST /api/upload/presigned-multipart/{id}/resume`
  returns the ETags of stored parts and fresh URLs for the missing ones, so a retry,
  pause, or page reload re-sends only what is missing.
- tus sessions last 24 hours and retry busy-server (`429`) responses.

The queue is kept per browser tab. After a reload, files already stored continue
processing without any action. Files still transferring need their `File` back:
re-attach the file or drop the same folder again.

**Multipart endpoints:**
- `POST /api/upload/presigned-multipart/init` : start session, returns part URLs
- `POST /api/upload/presigned-multipart/{id}/resume` : stored ETags and fresh URLs
- `POST /api/upload/presigned-multipart/complete` : finish and enqueue
- `DELETE /api/upload/presigned-multipart/{id}` : abort

**Tus endpoints:**
- `POST /api/upload/tus` : start session, begins S3 multipart
- `HEAD /api/upload/tus/{id}` : query offset (resumption)
- `PATCH /api/upload/tus/{id}` : append chunk
- `DELETE /api/upload/tus/{id}` : abort

### 3. Completion and queue admission

The API verifies the completed object's size and authoritative MIME type, records
the upload, and enqueues processing. CAS reuse happens only after the current
scan policy has been applied; an old clean result is not a bypass around the
worker's security gate.

### 4. Queue routing

| Condition | Queue |
|---|---|
| MIME is `video/*` | `upload-slow` |
| MIME is `text/*` or `image/*` | `upload-fast` |
| Any other type, size < 5 MiB | `upload-fast` |
| Any other type, size ≥ 5 MiB | `upload-slow` |

The fast and slow queues run on separate worker replicas with different resource limits, preventing a large video transcode from blocking a quick image upload.

### 5. Folder uploads

The browser traverses folders, preserves each file's relative directory path,
and sends every readable file through the normal per-file transfer strategy.
This avoids constructing one large in-memory archive and gives every file its
own resumable upload ID, progress, retry, and terminal status. An unreadable, unsupported, oversized, or
malicious entry is reported on that entry and does not cancel clean siblings.
Once all entries reach a terminal state, clean files can be added to the draft
even while failed entries remain visible.

When more than one file is added at once, from a folder or a multi-file
selection, the browser requests one bounded admission with
`POST /api/upload/groups`. Each admitted file sends that group ID together with
its stable upload ID. The server counts distinct IDs up to the role's advertised
limit, so a large batch does not consume the ordinary upload rate limit. After a browser reload, persisted queue
rows no longer hold their `File` objects; dropping the same folder again matches
interrupted rows by relative path, size and modification time, restores those
references, and resumes them in bulk. Folder structure and destination are stored
with each queue row, so they survive the reload.

`POST /api/upload/batch-zip` remains available for older API clients; the web
client does not use it. Its current
limits are advertised by `GET /api/upload/config`; clients should reuse one
`X-Upload-ID` when retrying a batch. Entry IDs are derived from that batch ID,
the tenant, relative path, and content hash, so a lost response can be retried
without duplicating committed uploads.

---

## Phase 2 : Background processing (ARQ worker)

### Stage 1 : Download and verify

The worker downloads the file from `quarantine/` to a local temp file and computes its SHA-256.

Real MIME type is detected from magic bytes (not trusted from the client).

### Stage 2 : Scan and strip (parallel)

Two operations run concurrently:

**YARA scan** : the file is matched against compiled rules in `YARA_RULES_DIR`. A match raises `ERR_MALWARE_DETECTED` and aborts the upload immediately. Setting `MALWARE_SCAN_ENABLED=false` skips both this scan and the MalwareBazaar lookup; metadata stripping still runs.

**Metadata strip** : image, PDF and Office files wait for a slot limited by `MAX_CONCURRENT_IMAGE_OPS`, for up to 5 minutes. The sanitizer then has 60 seconds to finish. Large batches therefore queue instead of failing. It removes identifying metadata:
- PDFs: embedded JavaScript, fonts, comments (pypdf)
- OLE2 / Office: VBA macro inspection via oletools; suspicious macros are rejected
- Images: EXIF data stripped (piexif, Pillow)
- Audio/Video: metadata tags stripped

**MalwareBazaar** runs before publication when `MALWAREBAZAAR_API_KEY` is set (the
service rejects anonymous requests, so without a key the lookup is skipped and
workers log a warning at startup). A clean verdict is cached by hash
for 7 days. With `MALWAREBAZAAR_FAIL_CLOSED=true` (the default) a lookup error
rejects publication, after three attempts for short-lived failures: the file fails
with an explicit retry-later error rather than being labeled as malware. In
fail-open mode an unavailable service is skipped and no verdict is cached.

### Stage 3 : Content-Addressed Storage (CAS)

The file is moved from `quarantine/` to `cas/{hmac(sha256)}`.

The CAS key is HMAC-SHA256 over the content hash using a dedicated key derived
from `SECRET_KEY` with HKDF (`CAS_HKDF_SALT` and `CAS_HKDF_INFO`), not the raw
hash. This prevents users from probing for known content by hash alone and
separates CAS signing from other uses of the application secret.

A Redis entry tracks the ref count. First upload: ref_count=1, storage quota incremented. Duplicate: ref_count+1, no extra storage.

The `Upload` row is updated to `status=clean, processing_status=pending`.

Scanning and sanitization always run in the same worker attempt: their result
exists only in that attempt's temporary file. A rerun after a worker restart
repeats both. A rerun after publication does nothing.

### Stage 4 : Post-scan processing

A follow-up `process_upload_post_scan` job runs:

It downloads the sanitized CAS object, so nothing is scanned or stripped twice.

**Thumbnail generation** (soft failure):
- Images, video: WebP, 640 px wide, 85% quality
- Documents: first-page render
- Stored at `thumbnails/{cas_id}.webp`

On completion, `processing_status=complete`. Failure after 3 attempts sets `processing_status=degraded`: the sanitized file is served without a thumbnail.

---

## Error handling summary

| Stage | Failure mode | Outcome |
|---|---|---|
| YARA match | Hard | Upload rejected, no retry |
| OLE macro detected | Hard | Upload rejected |
| Unexpected worker error (storage outage, …) | Transient | Retried up to 3 attempts with no client-visible failure, then `failed` and dead-lettered |
| Worker restart during processing | Transient | Job reruns from the start |
| Thumbnail failure | Soft | No thumbnail, upload completes |
| MalwareBazaar unreachable | Configurable | Three bounded attempts, then retry-later in fail-closed mode; skipped in fail-open mode |

---

## Monitoring

- Upload status: `GET /api/upload/{id}/status`
- Live progress updates: `GET /api/upload/events/{file_key}` (Server-Sent Events, per file)
- ARQ queue depth: check Redis keys `arq:queue:upload-fast` and `arq:queue:upload-slow`
