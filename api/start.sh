#!/bin/sh

set -e

# Wait for postgres to be ready
echo "Waiting for database..."
python wait_for_db.py

echo "Running migrations..."
alembic upgrade head

echo "Starting application..."
exec "$@"
