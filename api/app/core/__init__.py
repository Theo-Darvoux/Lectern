"""Core application modules grouped by functionality:
- common: shared constants, exceptions, natural_sorting, typing
- database: SQL engine, sessions, Redis, Lua scripts
- events: SSE, email, Meilisearch, rate limiting, processing lock
- media: MIME detection, avatar processor
- observability: Prometheus metrics, OpenTelemetry tracing
- sanitization: dangerous-char stripping, SanitizedStr, NameStr
- security: auth, worker tokens, CAS, scanning, sandboxing, file security
- storage: S3 and local storage backends
"""
