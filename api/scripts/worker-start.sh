#!/bin/sh

set -e

export PYTHONPATH="${PYTHONPATH:-/app}"

echo "Waiting for database..."
python scripts/wait_for_db.py

echo "Starting worker..."
exec "$@"
