# syntax=docker/dockerfile:1

# Single-stage: the UI is server-rendered Jinja, so there is no Node toolchain,
# no bundler, and no build artifact to stage in.
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Patch the OS layer: the base image lags Debian security updates (the container
# scan flags e.g. util-linux), so pull them explicitly at build time.
RUN apt-get update && \
    apt-get upgrade -y --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

# Install Python deps first so source changes don't bust the dependency layer.
# pip itself is upgraded first: the base image ships a pip with known CVEs
# that the container scan (rightly) flags.
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    # The runtime image never installs packages, so drop the installer stack:
    # base-image setuptools carries CVEs, and pip vendors a vulnerable msgpack
    # (flagged by the container scan) that even latest pip hasn't rev'd yet.
    pip uninstall -y pip setuptools

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

# --proxy-headers: behind a platform proxy (Render, Cloud Run) the client IP
# must come from X-Forwarded-For, or rate limiting and audit IPs all see the
# proxy. Trusting "*" is correct ONLY behind such a proxy — if you expose this
# container directly to the internet, the header becomes spoofable.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips "*"
