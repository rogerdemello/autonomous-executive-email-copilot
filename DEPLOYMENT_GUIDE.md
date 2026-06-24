# Deployment Guide

This project ships as a single container image: a multi-stage
[`Dockerfile`](Dockerfile) that compiles the React dashboard, installs the
Python runtime, runs as a non-root user, and serves the FastAPI app (with the
bundled dashboard) on **port 7860**.

## Quick deploy (Docker)

```bash
docker build -t exec-email-copilot .
docker run -p 7860:7860 exec-email-copilot
# or
docker compose up --build
```

Then:

- API: `http://localhost:7860/`
- Health: `http://localhost:7860/health`
- Docs: `http://localhost:7860/docs`
- Dashboard: `http://localhost:7860/dashboard/`

The container declares a `/health` healthcheck, so orchestrators (Docker,
Kubernetes, ECS, Cloud Run, etc.) get readiness signals for free.

## Resource sizing

| Resource | Recommended |
|----------|-------------|
| Memory   | 1–2 GB (more only if you run large benchmark sweeps) |
| CPU      | 1–2 vCPU |
| Port     | 7860 (container). Local `uvicorn` dev runs on 8000 by convention. |

## Configuration

All configuration is environment-driven and read through `env/config.py`
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

## Platform notes

The image is a standard Linux container and runs on any container host:

- **Cloud Run / App Runner / Fly.io / Render**: point the platform at the
  `Dockerfile`, expose port 7860, and set the env vars above as secrets.
- **Kubernetes**: use the `/health/live` and `/health/ready` probes for liveness
  and readiness; mount provider keys as secrets.
- **Hugging Face Spaces** (optional): Spaces can host the Docker image. Add a
  Space metadata header to the README (`sdk: docker`, `app_port: 7860`) and push
  the repo to the Space remote; configure provider keys as Space secrets.

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
python inference.py --task easy_classification --max-steps 20   # CLI runner (no key needed)
```
