# Upload Pipeline

Every file upload goes through two phases: a **prescan gateway** (synchronous, in the API request) followed by **background processing** (asynchronous, in an ARQ worker).

---

## Overview

```
Client                       API                          ARQ Worker
  │                           │                               │
  ├─ hash file (WebCrypto) ──►│                               │
  ├─ validate (size/type) ───►│                               │
  ├─ transfer to S3 ─────────►│ quarantine/{user}/{id}/file   │
  ├─ complete upload ────────►│                               │
  │                           ├── enqueue ───────────────────►│
  │◄── upload_id, status ─────┤                               │
  │                           │                               ├─ download & verify hash
  │                           │                               ├─ YARA scan
  │                           │                               ├─ strip metadata
  │                           │                               ├─ move to CAS
  │                           │                               ├─ compress
  │                           │                               ├─ generate thumbnail
  │◄── SSE: processing_status ┤◄── SSE event ─────────────────┤
```

---

## Phase 1 — Prescan gateway (API)

### 1. Initiation

`POST /api/upload/init` validates:
- File size against per-type and global limits
- MIME type against the allowlist
- User storage quota
- Pending upload cap (prevents queue flooding)

A database `Upload` row is created with `status=staging` and a `quarantine_key` pointing to `quarantine/{user_id}/{upload_id}/{filename}` in S3.

### 2. Transfer

The client chooses a transfer strategy based on file size:

| Size | Strategy |
|---|---|
| < 5 MiB | Presigned `PUT` directly to S3 |
| 5–100 MiB | Resumable [tus](https://tus.io/) protocol (8 MiB chunks → S3 multipart) |
| > 100 MiB | Presigned multipart URLs (8 MiB parts) |

The browser hashes the file with WebCrypto (SHA-256) during upload. This hash is sent to the API on completion.

**Tus endpoints:**
- `POST /api/upload/tus` : start session, begins S3 multipart
- `HEAD /api/upload/tus/{id}` : query offset (resumption)
- `PATCH /api/upload/tus/{id}` : append chunk
- `DELETE /api/upload/tus/{id}` : abort

### 3. CAS deduplication check

Before enqueuing, the API checks whether the SHA-256 has already been scanned and stored in CAS (Content-Addressed Storage). If a clean copy exists:
- Processing is skipped
- The upload is linked to the existing CAS entry immediately
- Ref count is incremented, no additional storage is consumed

### 4. Queue routing

| Condition | Queue |
|---|---|
| MIME is `text/*` or `image/*` and not heavy | `upload-fast` |
| MIME is video, PDF, ZIP, EPUB, or Office | `upload-slow` |
| Fallback: size < 5 MiB | `upload-fast` |
| Fallback: size ≥ 5 MiB | `upload-slow` |

The fast and slow queues run on separate worker replicas with different resource limits, preventing a large video transcode from blocking a quick image upload.

---

## Phase 2 : Background processing (ARQ worker)

### Stage 1 : Download and verify

The worker downloads the file from `quarantine/` to a local temp file, recomputes the SHA-256, and checks it against the expected hash from Phase 1. This ensures integrity across retries.

Real MIME type is detected from magic bytes (not trusted from the client).

### Stage 2 : Scan and strip (parallel)

Two operations run concurrently:

**YARA scan** : the file is matched against compiled rules in `YARA_RULES_DIR`. A match raises `ERR_MALWARE_DETECTED` and aborts the upload immediately.

**Metadata strip** : removes identifying metadata:
- PDFs: embedded JavaScript, fonts, comments (pypdf)
- OLE2 / Office: VBA macro inspection via oletools; suspicious macros are rejected
- Images: EXIF data stripped (piexif, Pillow)
- Audio/Video: metadata tags stripped

**MalwareBazaar** is checked asynchronously (default). After the YARA gate passes, a `check_bazaar` background job is enqueued : it does not block the upload. If MalwareBazaar later identifies a threat, the upload is flagged and the user is notified. Set `MALWAREBAZAAR_FAIL_CLOSED=true` to reject uploads when MalwareBazaar is unreachable.

### Stage 3 : Content-Addressed Storage (CAS)

The file is moved from `quarantine/` to `cas/{hmac(sha256)}`.

The CAS key is `HMAC-SHA256(file_sha256, SECRET_KEY)` : not the raw hash. This prevents users from probing whether a specific file exists in the store without possessing the file.

A Redis entry tracks the ref count. First upload: ref_count=1, storage quota incremented. Duplicate: ref_count+1, no extra storage.

The `Upload` row is updated to `status=clean, processing_status=pending`.

### Stage 4 : Post-scan processing

A follow-up `process_upload_post_scan` job runs:

**Compression** (soft failure : original served if this fails):
- Images: PNG/JPEG re-encoded at reduced quality
- PDF: quality reduction (controlled by `PDF_QUALITY`, default 75; `100` keeps images lossless — font subsetting and object packing only)
- Video: FFmpeg transcode using `VIDEO_COMPRESSION_PROFILE` (none | light | medium | aggressive | heavy | extreme)

**Thumbnail generation** (soft failure):
- Images, video: WebP, 640 px wide, 85% quality
- Documents: first-page render
- Stored at `thumbnails/{cas_id}.webp`

On completion, `processing_status=complete`. Failure after 3 ARQ retries sets `processing_status=degraded` : the original uncompressed file is served.

---

## Error handling summary

| Stage | Failure mode | Outcome |
|---|---|---|
| Hash mismatch | Hard | Upload rejected, retried |
| YARA match | Hard | Upload rejected, no retry |
| OLE macro detected | Hard | Upload rejected |
| CAS upload failure | Hard | ARQ retries (max 3), then `degraded` |
| Compression failure | Soft | Original served, upload completes |
| Thumbnail failure | Soft | No thumbnail, upload completes |
| MalwareBazaar unreachable | Configurable | Fail-open (default) or fail-closed |

---

## Monitoring

- Upload status: `GET /api/upload/{id}/status`
- Live progress updates: `GET /api/upload/sse` (Server-Sent Events, per user)
- ARQ queue depth: check Redis keys `arq:queue:upload-fast` and `arq:queue:upload-slow`
