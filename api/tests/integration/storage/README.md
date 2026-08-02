# SeaweedFS integration tests

These tests exercise the real S3-compatible SeaweedFS gateway through the
application storage facade. They are marked `integration` and remain excluded
from the normal hermetic test run.

## Coverage

- Backend initialization and persistent-client lifecycle
- Byte, file-like, empty, Unicode-key, and overwrite behavior
- Object metadata, info, existence, CAS lookup, listing, copy, move, and deletion
- Raw, hashed, bounded, gzip-decoded, range-prefix, and streaming reads
- Concurrent object operations
- Manual and helper-driven multipart uploads, including small-file fallback
- Multipart listing, completion, abort, injected-failure cleanup, and idempotency
- Presigned PUT, GET, response overrides, and multipart parts over real HTTP
- Invalid-credential rejection
- Permanent public URL construction
- The upload worker's real download, SHA-256, MIME, polyglot, and size-limit path

Every session creates a randomly named bucket and deletes it afterward. The
suite refuses remote endpoints unless `SEAWEEDFS_ALLOW_REMOTE=1` is explicitly
set, so it cannot silently clean a production bucket.

## Run locally

From the repository root:

```bash
uv sync --project api --frozen --extra dev
./api/scripts/run-seaweedfs-integration-tests.sh
```

The script starts a disposable SeaweedFS container on host ports `18333` and
`19333`. Override them when needed:

```bash
SEAWEEDFS_TEST_PORT=28333 \
SEAWEEDFS_MASTER_PORT=29333 \
./api/scripts/run-seaweedfs-integration-tests.sh
```

To target an already-running disposable instance:

```bash
cd api
SEAWEEDFS_INTEGRATION=1 \
SEAWEEDFS_TEST_ENDPOINT=127.0.0.1:8333 \
SEAWEEDFS_TEST_ACCESS_KEY=minioadmin \
SEAWEEDFS_TEST_SECRET_KEY=minioadmin \
uv run pytest -m integration tests/integration/storage -ra
```

For a remote CI service, `SEAWEEDFS_ALLOW_REMOTE=1` is also required. Never
point these destructive tests at a shared or production SeaweedFS endpoint.
