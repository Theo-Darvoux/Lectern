#!/bin/sh

set -e

export PYTHONPATH="${PYTHONPATH:-/app}"

echo "Starting worker..."
exec "$@"
