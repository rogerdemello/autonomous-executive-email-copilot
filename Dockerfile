# syntax=docker/dockerfile:1

# --- Stage 1: build the React dashboard -> /dashboard/dist ---
FROM node:20-slim AS dashboard-builder
WORKDIR /dashboard
# Install deps first for layer caching (package-lock.json optional).
COPY dashboard/package.json dashboard/package-lock.json* ./
RUN npm ci || npm install
COPY dashboard/ ./
RUN npm run build

# --- Stage 2: Python runtime ---
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install Python deps first so source changes don't bust the dependency layer.
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Application source, then the pre-built dashboard from stage 1.
COPY . .
COPY --from=dashboard-builder /dashboard/dist ./dashboard/dist

# Run as a non-root user; ensure the app dir (incl. runtime SQLite dirs) is writable.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data /app/env/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 7860

# Bind to $PORT when the host injects one (Render, Cloud Run, Fly.io, …),
# falling back to 7860 for local `docker run`. Shell form so $PORT expands.
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import os,httpx; httpx.get('http://localhost:%s/health' % os.environ.get('PORT','7860'), timeout=5).raise_for_status()"

CMD uvicorn env.api:app --host 0.0.0.0 --port ${PORT:-7860}
