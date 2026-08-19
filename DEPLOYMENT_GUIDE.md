# Deployment Guide

This project ships as a single container image: a single-stage
[`Dockerfile`](Dockerfile) that installs the Python runtime, runs as a non-root
user, and serves both the JSON API and the server-rendered UI on **port 8000**.
There is no Node toolchain and no frontend build step — the UI is Jinja
templates rendered by the same process.

## Quick deploy (Docker)

```bash
docker build -t exec-email-copilot .
docker run -p 8000:8000 exec-email-copilot
# or
docker compose up --build
```

Then:

- Product UI (landing → login → inbox): `http://localhost:8000/`
- Health: `http://localhost:8000/health`
- API docs: `http://localhost:8000/docs`

The container declares a `/health` healthcheck, so orchestrators (Docker,
Kubernetes, ECS, Cloud Run, etc.) get readiness signals for free.

## Resource sizing

| Resource | Recommended |
|----------|-------------|
| Memory   | 1–2 GB (more only if you run large benchmark sweeps) |
| CPU      | 1–2 vCPU |
| Port     | 8000 (container and local `uvicorn`; `$PORT` overrides it). |

## Configuration

All configuration is environment-driven and read through `app/core/config.py`
(see [.env.example](.env.example) for the full list). Nothing is required for the
deterministic agents; the LLM agent needs a provider.

| Variable | Purpose | Example |
|----------|---------|---------|
| `OPENAI_API_KEY` | LLM provider API key | `sk-...` |
| `API_BASE_URL` | LLM provider endpoint (OpenAI-compatible) | `https://api.openai.com/v1` |
| `MODEL_NAME` | Model id | `gpt-4o-mini` |
| `AZURE_OPENAI_*` | Native Azure OpenAI settings (endpoint/key/version/deployment) | see `.env.example` |
| `API_AUTH_TOKEN` | When set, mutating routes require a bearer token / `X-API-Key` | — |
| `CORS_ORIGINS` | Allowed browser origins (default `*`) | `https://app.example.com` |
| `RATE_LIMIT_PER_MINUTE` | Per-IP request cap (default `0` = off) | `120` |

When exposing the API to an untrusted network, set `API_AUTH_TOKEN`,
`CORS_ORIGINS`, and `RATE_LIMIT_PER_MINUTE`. See [SECURITY.md](SECURITY.md).

## Deploy to Render

A [`render.yaml`](render.yaml) Blueprint is included. The container binds the
`$PORT` Render injects automatically (falling back to 8000 locally), so no port
config is required.

1. Push this repo to GitHub.
2. In Render: **New → Blueprint**, select the repo. Render reads `render.yaml`
   and provisions one Docker **web service** (FastAPI, which also serves the
   server-rendered product UI — no separate frontend).
3. Set any secrets (provider keys, `API_AUTH_TOKEN`, `CORS_ORIGINS`) in the
   service's **Environment** tab — they are declared `sync: false` so they live
   in Render, not git.
4. Render health-checks `/health` and serves the app at the assigned URL; the
   product UI is the root (`/` → `/login` → `/app/inbox`).

The default SQLite store sits on the container's ephemeral disk and resets on
redeploy. For durable data, set `DATABASE_URL` to a Postgres URL (e.g. a Render
Postgres instance).

## Other platforms

The image is a standard Linux container and runs on any container host:

- **Cloud Run / App Runner / Fly.io**: point the platform at the `Dockerfile`.
  These hosts also inject `$PORT`, which the container honors.
- **Kubernetes**: use the `/health/live` and `/health/ready` probes for liveness
  and readiness; mount provider keys as secrets.

## Observability

A Prometheus/Grafana stack is provided under [telemetry/](telemetry/). Bring it
up alongside the app with:

```bash
docker compose -f telemetry/docker-compose.observability.yml up
```

Metrics are exposed at `/metrics`; see the ops [runbook](docs/RUNBOOK.md).

## Pre-deploy checklist

```bash
ruff check . && ruff format --check .     # lint/format
python -m pytest -q                        # tests
docker build -t exec-email-copilot .       # image builds
python research/inference.py --task easy_classification --max-steps 20   # CLI runner (no key needed)
```
