# ── Stage 1: build dependencies ───────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build tools for packages that need compilation (e.g. asyncpg)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src/ ./src/
COPY migrations/ ./migrations/
COPY alembic.ini ./
RUN pip install --upgrade pip \
    && pip install --no-cache-dir hatchling \
    && pip install --no-cache-dir ".[dev]" --target /deps


# ── Stage 2: production image ─────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Security: run as non-root
RUN groupadd -r botuser && useradd -r -g botuser botuser

WORKDIR /app

# Runtime system deps (libmagic for file type detection)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /deps /usr/local/lib/python3.11/site-packages

# Copy application source
COPY src/ ./src/
COPY migrations/ ./migrations/
COPY alembic.ini ./

# Ensure non-root user owns the app directory
RUN chown -R botuser:botuser /app

USER botuser

# Healthcheck via the /healthz endpoint exposed by uvicorn
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${APP_PORT:-8080}/healthz')" \
    || exit 1

EXPOSE 8080

# Default: run the bot (polling + webhook server)
CMD ["python", "-m", "src.main"]
