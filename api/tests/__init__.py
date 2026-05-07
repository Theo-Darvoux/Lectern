import atexit
import os
import threading

# Hack for Python 3.12 async thread pooling and aiosqlite threads
# keeping the pytest process open. We disable Python's strict thread shutdown waiting.
try:
    import concurrent.futures.thread

    atexit.unregister(concurrent.futures.thread._python_exit)
except Exception:
    pass

threading._shutdown = lambda: None


# Set required env vars before any app module is imported (settings are built at import time).
os.environ.setdefault("ONLYOFFICE_JWT_SECRET", "test-onlyoffice-jwt-secret-for-pytest-only")
os.environ.setdefault("ONLYOFFICE_FILE_TOKEN_SECRET", "test-onlyoffice-file-token-secret-pytest")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite://")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest-only-not-production")
