# ── Stage 1: build the virtualenv ────────────────────────────────────────────
ARG DEBIAN_SNAPSHOT=20260801T000000Z
FROM python:3.12-slim-trixie@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de AS builder
ARG DEBIAN_SNAPSHOT

COPY --from=ghcr.io/astral-sh/uv:0.12.3-python3.13-dhi@sha256:2b56d2665e5591ce8a1e527b1471637fba03e46324236e4a1bc7c591f56a4edf /usr/local/bin/uv /usr/local/bin/uv

WORKDIR /app

RUN printf '%s\n' \
    "deb [check-valid-until=no] https://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}/ trixie main" \
    "deb [check-valid-until=no] https://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT}/ trixie-security main" \
    > /etc/apt/sources.list.d/debian-snapshot.list && \
    rm -f /etc/apt/sources.list.d/debian.sources

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libyara-dev \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir /venv
COPY pyproject.toml uv.lock ./
ENV UV_PROJECT_ENVIRONMENT=/venv
ENV UV_PYTHON_DOWNLOADS=never
RUN uv sync --frozen --no-dev --python /usr/local/bin/python3.12

# Strip stub/test files from the venv. We intentionally do NOT pre-compile
# bytecode: the runtime sets PYTHONDONTWRITEBYTECODE=1, so shipped .pyc files
# would never be reused — they'd only inflate the image.
RUN find /venv -name "*.pyi" -delete && \
    find /venv -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true && \
    find /venv -type d \( -name "tests" -o -name "test" \) -exec rm -rf {} + 2>/dev/null || true && \
    rm -rf /venv/include

# ── Stage 2: common runtime base (shared by api + worker targets) ────────────
# libyara10: YARA runtime — imported by app.core.security.scanner at API startup, and
# used by the workers during file scanning. Kept here so both targets have it.
FROM python:3.12-slim-trixie@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de AS runtime-base
ARG DEBIAN_SNAPSHOT

WORKDIR /app

RUN printf '%s\n' \
    "deb [check-valid-until=no] https://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}/ trixie main" \
    "deb [check-valid-until=no] https://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT}/ trixie-security main" \
    > /etc/apt/sources.list.d/debian-snapshot.list && \
    rm -f /etc/apt/sources.list.d/debian.sources

RUN apt-get update && apt-get install -y --no-install-recommends \
    libyara10 \
    bubblewrap \
    util-linux \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -r appuser && useradd -r -g appuser -m appuser && \
    chown appuser:appuser /app && \
    mkdir -p /var/lib/lectern/processing && \
    chown -R appuser:appuser /var/lib/lectern

COPY --from=builder --chown=appuser:appuser /venv /venv
ENV PATH="/venv/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH="/app"

USER appuser

COPY --chown=appuser:appuser . .
RUN chmod +x scripts/start.sh scripts/worker-start.sh

EXPOSE 8000

# ── Stage 3a: API (web-facing) ───────────────────────────────────────────────
# The API performs batch-archive and avatar parsing through the same short-lived
# Bubblewrap child boundary as workers. Heavy converters remain worker-only.
FROM runtime-base AS api

ENTRYPOINT ["./scripts/start.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ── Stage 3b: Worker ─────────────────────────────────────────────────────────
# Adds the document/media processing toolchain used by the upload pipeline:
#   poppler-utils & poppler-data (PDF rendering & CMaps), ghostscript (PDF fallback/compression),
#   ffmpeg (audio/video), exiftool (metadata strip), libreoffice-nogui (Office→PDF thumbnails),
#   librsvg2-bin (SVG→PNG thumbnails), and standard TrueType fallback fonts.
# This is the default build target (last stage), so a plain `docker build`
# yields the full-capability image.
FROM runtime-base AS worker

USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    ghostscript \
    ffmpeg \
    libimage-exiftool-perl \
    libreoffice-nogui \
    librsvg2-bin \
    poppler-utils \
    poppler-data \
    fonts-dejavu-core \
    fonts-freefont-ttf \
    fonts-liberation \
    fonts-lmodern \
    fonts-texgyre \
    fonts-crosextra-carlito \
    fonts-crosextra-caladea \
    fonts-noto-core \
    fonts-urw-base35 \
    && rm -rf /var/lib/apt/lists/*
USER appuser

ENTRYPOINT ["./scripts/worker-start.sh"]
CMD ["arq", "app.workers.settings.WorkerSettings"]
