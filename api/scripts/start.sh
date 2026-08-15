#!/bin/sh

set -e

export PYTHONPATH="${PYTHONPATH:-/app}"

# Wait for postgres to be ready
echo "Waiting for database..."
python scripts/wait_for_db.py

# Apply database migrations unless explicitly disabled.
# Set RUN_MIGRATIONS=false to skip (e.g. when migrations are run out-of-band).
RUN_MIGRATIONS="${RUN_MIGRATIONS:-true}"

if [ "$RUN_MIGRATIONS" = "true" ]; then
    echo "Running migrations..."
    if ! alembic upgrade head; then
        echo ""
        echo "############################################################"
        echo "##                                                        ##"
        echo "##   DATABASE MIGRATION FAILED — REFUSING TO START API    ##"
        echo "##                                                        ##"
        echo "##   The schema is not up to date. The application will   ##"
        echo "##   NOT start to avoid running against a broken schema.  ##"
        echo "##                                                        ##"
        echo "##   Check the Alembic output above, fix the migration,   ##"
        echo "##   then redeploy. To bypass migrations entirely, set    ##"
        echo "##   RUN_MIGRATIONS=false.                                ##"
        echo "##                                                        ##"
        echo "############################################################"
        echo ""
        exit 1
    fi
else
    echo "RUN_MIGRATIONS=$RUN_MIGRATIONS — skipping database migrations."
fi

echo "Starting application..."
exec "$@"
