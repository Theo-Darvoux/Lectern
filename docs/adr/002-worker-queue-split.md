# ADR 002 — Split upload queues: fast and slow workers

## Status

Accepted

## Context

Upload processing spans a wide range of workloads:
- A 20 KB PNG image: a few milliseconds of YARA scan + EXIF strip.
- A 500 MB lecture video: minutes of FFmpeg transcoding, YARA scan over a large buffer.

With a single shared queue, a batch of video transcodes would saturate all worker slots. An instructor submitting a corrected PDF minutes before a lecture would wait behind the video queue. The two workloads have fundamentally different resource profiles (CPU-bound transcoding vs. I/O-bound small file processing).

## Decision

Three worker tiers:

| Worker | Queue | MIME types / size | Concurrency | Memory |
|---|---|---|---|---|
| `worker-fast` | `upload-fast` | `text/*`, `image/*` under 5 MiB | 4 jobs × 2 replicas | 1 GB / replica |
| `worker-slow` | `upload-slow` | video, PDF, Office, ZIP, ≥ 5 MiB | 2 jobs × 2 replicas | 2 GB / replica |
| `worker` | default | non-upload tasks + fallback | unlimited | 2 GB |

Queue routing runs at job enqueue time in `routers/upload/helpers.py`. The logic prioritises MIME type over size: a 3 MiB MP4 goes to `upload-slow` even though it is under the 5 MiB threshold.

Replicas are configurable via `WORKER_FAST_REPLICAS` and `WORKER_SLOW_REPLICAS` env vars, allowing the slow queue to scale horizontally during high-load events without touching the fast path.

## Consequences

- **Priority isolation**: a video transcode backlog cannot starve image or text uploads.
- **Resource right-sizing**: `worker-fast` replicas use half the memory of `worker-slow`, keeping costs proportional.
- **Operational complexity**: three worker processes to monitor instead of one. The main `worker` service provides a fallback for both queues if dedicated workers are down.
- **Misclassification risk**: MIME detection is based on magic bytes, not file extension. A deliberately mislabelled file will be reclassified at the download stage and may land on the wrong queue for a single job before the correct routing applies on retry.
