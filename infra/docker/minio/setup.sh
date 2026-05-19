#!/bin/sh
set -e

# Wait for MinIO to be ready
sleep 5

# Alias local for standard commands
mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"

# Create bucket if it doesn't exist
mc mb --ignore-existing local/wikint

# Ensure it's private
mc anonymous set none local/wikint

# Allow anonymous reads for branding assets (logo, favicon)
mc anonymous set download local/wikint/branding

# Apply GLOBAL CORS configuration (Required for open-source MinIO)
echo "Applying Global CORS configuration via Admin API..."
# Allow specific origins for development
mc admin config set local/ api cors_allow_origin="http://localhost,http://localhost:3000,http://localhost:8000"

echo "MinIO setup successful."
