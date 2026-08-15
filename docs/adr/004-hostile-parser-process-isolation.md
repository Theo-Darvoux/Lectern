# ADR 004 — Isolate native hostile-file parsers from long-lived workers

## Status

Accepted — parser isolation implemented; outer worker hardening remains

## Context

Uploaded bytes are adversarial. External converters such as ffmpeg,
Ghostscript, LibreOffice, and rsvg run through Bubblewrap with namespaces,
resource limits, bounded output capture, and cancellation-safe process cleanup.
Native parsing of hostile upload inputs now runs in a short-lived Bubblewrap
child: YARA, MIME signature/archive inspection, Pillow, pikepdf/qpdf, oletools, and
batch ZIP extraction. The child receives only the selected input, a bounded
writable processing directory, a minimal non-secret environment, no network,
and inherited CPU/memory/PID/file-size limits. Parent code validates the child
result and every extracted output before use.

The API and worker containers need namespace support for Bubblewrap. Their
default capabilities are dropped; the tested minimal capability set is
`SETUID`, `SETGID`, and `SETFCAP`, with `no-new-privileges`. Docker's default
seccomp/AppArmor profiles do not permit the required unprivileged namespace
operations, so those profiles remain unconfined.

## Implemented boundary

The per-file child boundary has:

- no network access or application/database credentials;
- read-only access to one input and a bounded writable output directory;
- separate user, mount, PID, IPC, UTS, cgroup, and network namespaces plus
  PID/memory/CPU/file-size limits;
- an authenticated result protocol that returns only validated metadata and output paths;
- hard termination and cleanup on timeout, cancellation, crash, or worker lease loss.

Default container capabilities are dropped and `no-new-privileges` is set. A
production-container smoke test exercises the exact UID, capability, and LSM
profile used by Compose.

## Remaining direction

Bubblewrap still creates namespaces inside the credentialed API and ARQ
containers. `SYS_ADMIN` is not granted, but the unconfined syscall/LSM profiles
and three identity-mapping capabilities are broader than the application needs
outside sandbox launch. A dedicated credential-free processing service or a
tested narrow seccomp/AppArmor policy remains the preferred next boundary.

## Consequences

Compromise of the short-lived parser must first escape its Bubblewrap boundary
before reaching a credentialed outer process. The outer namespace-launch policy
remains a defense-in-depth residual. A dedicated credential-free processing
service can remove those permissions from application containers and narrow
network access independently.
