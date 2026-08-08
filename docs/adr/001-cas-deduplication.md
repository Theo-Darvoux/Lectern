# ADR 001 — Content-Addressed Storage for file deduplication

## Status

Accepted

## Context

Course materials are frequently re-uploaded: a PDF distributed to multiple cohorts, a corrected version with a typo fix, or the same reference document added to several directories. Without deduplication, each upload would consume separate storage, increasing cost and quota exhaustion for large files.

A naïve deduplication by filename or path is unreliable. Two identically-named files may have different content; two differently-named files may be identical.

## Decision

Files are stored under a **content-addressed key** derived from their SHA-256
hash: `cas/{HMAC-SHA256(sha256, HKDF(SECRET_KEY, CAS_HKDF_*))}`.

The HMAC wrapper over the raw hash serves a specific purpose: it prevents an
attacker from probing whether known content exists without possessing it. HKDF
provides a domain-separated CAS key instead of reusing `SECRET_KEY` directly.

A Redis counter tracks ref counts per CAS key. On first upload the file is physically stored and the user's quota incremented. On duplicate upload the counter is incremented and no additional storage is consumed.

On deletion, the counter is decremented. When it reaches zero the object is deleted from S3 and the quota is reclaimed.

## Consequences

- **Storage costs are bounded by unique content**, not upload frequency.
- **YARA rule updates** require care: if a previously-clean file is later flagged by new rules, the cached CAS entry still serves the old scan result. The `cas_max_age_seconds` config controls how long a CAS entry is trusted before re-scanning.
- **Cross-user deduplication** happens transparently — two users uploading the same file share one physical object. This is intentional on a shared academic platform.
- **Quota accounting** counts logical size (per-user ref count × file size), not physical storage. A user who uploads a file already present in CAS still has their quota incremented.
