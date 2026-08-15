"""Shared admission limits for the legacy batch-ZIP upload path."""

BATCH_MAX_ZIP_SIZE_BYTES = 500 * 1024 * 1024
BATCH_MAX_TOTAL_EXTRACTED_BYTES = 2 * 1024**3
BATCH_MAX_FILES = 200
BATCH_MAX_FILES_PRIVILEGED = 2_000
BATCH_MAX_COMPRESSION_RATIO = 100
BATCH_MAX_PATH_DEPTH = 20
UPLOAD_GROUP_TTL_SECONDS = 48 * 60 * 60

# Multipart framing adds a small amount of data around the ZIP itself. The route
# still enforces BATCH_MAX_ZIP_SIZE_BYTES against the actual file stream.
BATCH_REQUEST_BODY_LIMIT_BYTES = BATCH_MAX_ZIP_SIZE_BYTES + 1024 * 1024
