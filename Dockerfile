# syntax=docker/dockerfile:1

# Single-stage: the UI is server-rendered Jinja, so there is no Node toolchain,
# no bundler, and no build artifact to stage in.
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install Python deps first so source changes don't bust the dependency layer.
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as a non-root user; ensure the app dir (incl. the runtime SQLite dir) is writable.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Bind to $PORT when the host injects one (Render, Cloud Run, Fly.io, …),
# falling back to 8000 for local `docker run`. Shell form so $PORT expands.
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import os,httpx; httpx.get('http://localhost:%s/health' % os.environ.get('PORT','8000'), timeout=5).raise_for_status()"

CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
