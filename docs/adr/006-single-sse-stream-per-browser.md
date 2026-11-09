# ADR 006: One SSE stream per browser

## Status

Accepted

## Context

Live updates reached the browser through several paths. A master stream carried notifications, PR events, and browse topics, and each file in processing opened its own upload stream. The server allowed one master stream per account, held by a Redis lease with first-connection-wins semantics.

That design had three operational problems:

- A second device received `503` responses indefinitely and never got live updates.
- A reconnect after sleep or a network drop was rejected until the server noticed the dead connection.
- Every topic change reopened the stream, raced the lease, and refetched all data on every open.

Upload streams also consumed up to four extra connections per browser and duplicated quotas, pub/sub handoff, replay, and an SSE parser.

## Decision

Each browser holds exactly one stream, on `GET /api/events/sse`.

- **Scope.** Web Locks elect one leader tab, which opens the stream for the union of all tabs' channels. Events reach the other tabs over `BroadcastChannel`. Browsers without Web Locks fall back to one stream per tab.
- **Replacement, not rejection.** The request carries a client id. A new stream replaces any older stream with the same user and client id, including across API processes through the Redis fan-out. This removes the lease.
- **Limits.** Each process allows 8 streams per user and 2,000 in total. Streams end after one hour so a revoked session cannot keep receiving events.
- **Topic changes.** The client debounces subscription changes, then opens the replacement before closing the old stream. Channels carried by both streams need no reconciliation, so only newly gained channels refetch.
- **Failure handling.** Delivery stays best-effort. A stream that overflows, or that misses events during a Redis outage, is closed. The client reconnects and reconciles all channels from the HTTP APIs.
- **Stale topics.** The server ignores unknown, malformed, or deleted topics. One stale interest must not cost a browser its notifications.
- **Uploads.** Workers publish progress to a per-user `uploads` channel. `GET /api/upload/status/{file_key}` is the source of truth. The client reconciles against it at start, after any gap, and on a slow timer. The per-file stream and its Redis event log are gone.

## Consequences

- Each browser uses one long-lived connection, however many tabs or uploads it has.
- Clients see the latest upload stage only. Past stages are not replayed after a reload.
- The per-user limit is enforced per process, so the global ceiling scales with the worker count.
- Topics still travel in the URL. A topic change costs a new request, but no longer costs a delivery gap or a full refetch.
