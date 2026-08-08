# ADR 004 — Isolate native hostile-file parsers from long-lived workers

## Status

Proposed — production residual risk

## Context

Uploaded bytes are adversarial. External converters such as ffmpeg,
Ghostscript, LibreOffice, and rsvg run through Bubblewrap with namespaces,
resource limits, bounded output capture, and cancellation-safe process cleanup.
Some native libraries cannot use that boundary today: YARA, libmagic, Pillow,
pikepdf/qpdf, and oletools are imported into the long-lived ARQ worker and parse
untrusted files in threads. A memory-corruption flaw in one of those libraries
would therefore execute inside a reusable worker container.

The worker container also currently needs broad namespace support for
Bubblewrap (`SYS_ADMIN`, unconfined seccomp/AppArmor). Those permissions make
the consequence of an in-process native parser compromise materially worse.

## Required direction

Move every native hostile-file parser behind a short-lived, per-file process or
dedicated low-privilege processing service. The child boundary must have:

- no network access or application/database credentials;
- read-only access to one input and a bounded writable output directory;
- a restrictive seccomp/AppArmor profile, dropped capabilities, PID/memory/CPU/file-size limits;
- an authenticated result protocol that returns only validated metadata and output paths;
- hard termination and cleanup on timeout, cancellation, crash, or worker lease loss.

Once parser isolation no longer depends on namespaces created inside the ARQ
container, remove `SYS_ADMIN` and the unconfined security profiles from that
container as a separate deployment change.

## Consequences

The current Bubblewrap boundary remains valuable for subprocess-based tools,
but it is not a complete sandbox for the upload pipeline. A correct migration
changes worker topology, credential distribution, cancellation, observability,
and image packaging; replacing it with an ad-hoc fork or partial wrapper during
a release audit would create misleading isolation. Until this ADR is
implemented, native-parser compromise is an explicit residual architectural
risk rather than a locally closed finding.
