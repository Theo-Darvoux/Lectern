"""Redis key prefixes shared by the upload API and its workers."""

# Latest pipeline event per file key, read by the status route.
UPLOAD_STATUS_PREFIX = "upload:status:"
# Sorted set of a user's pending upload keys, bounding concurrent uploads.
UPLOAD_QUOTA_PREFIX = "quota:uploads:"
# Set when a user cancels an upload, so in-flight workers stop.
UPLOAD_CANCEL_PREFIX = "upload:cancel:"
