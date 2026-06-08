# syntax=docker/dockerfile:1

# ── Stage 1: build the virtualenv ────────────────────────────────────────────
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libyara-dev \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir /venv
COPY pyproject.toml uv.lock ./
ENV UV_PROJECT_ENVIRONMENT=/venv
RUN uv sync --frozen --no-dev

# Strip stub/test files from the venv. We intentionally do NOT pre-compile
# bytecode: the runtime sets PYTHONDONTWRITEBYTECODE=1, so shipped .pyc files
# would never be reused — they'd only inflate the image.
RUN find /venv -name "*.pyi" -delete && \
    find /venv -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true && \
    find /venv -type d \( -name "tests" -o -name "test" \) -exec rm -rf {} + 2>/dev/null || true && \
    rm -rf /venv/include

# ── Stage 2: common runtime base (shared by api + worker targets) ────────────
# libyara10: YARA runtime — imported by app.core.scanner at API startup, and
# used by the workers during file scanning. Kept here so both targets have it.
FROM python:3.12-slim AS runtime-base

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libyara10 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -r appuser && useradd -r -g appuser -m appuser && \
    chown appuser:appuser /app && \
    mkdir -p /var/lib/lectern && chown appuser:appuser /var/lib/lectern

COPY --from=builder --chown=appuser:appuser /venv /venv
ENV PATH="/venv/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH="/app"

USER appuser

COPY --chown=appuser:appuser . .
RUN chmod +x start.sh worker-start.sh

EXPOSE 8000

# ── Stage 3a: API (web-facing) ───────────────────────────────────────────────
# Slim image: serves HTTP only. None of the document/media tooling is reachable
# from the request path (it lives entirely in app/workers + app/core/file_security),
# so it is deliberately excluded here to keep the public-facing image small.
FROM runtime-base AS api

ENTRYPOINT ["./start.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ── Stage 3b: Worker ─────────────────────────────────────────────────────────
# Adds the document/media processing toolchain used by the upload pipeline:
#   ghostscript (PDF), ffmpeg (audio/video), bubblewrap (sandbox),
#   exiftool (metadata strip), libreoffice-nogui (Office→PDF thumbnails),
#   librsvg2-bin (SVG→PNG thumbnails).
# This is the default build target (last stage), so a plain `docker build`
# yields the full-capability image.
FROM runtime-base AS worker

USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    ghostscript \
    ffmpeg \
    bubblewrap \
    libimage-exiftool-perl \
    libreoffice-nogui \
    librsvg2-bin \
    && rm -rf /var/lib/apt/lists/*
USER appuser

ENTRYPOINT ["./worker-start.sh"]
CMD ["arq", "app.workers.settings.WorkerSettings"]
