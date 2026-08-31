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
| `ENVIRONMENT` | `production` makes the app refuse to boot on unsafe config, marks cookies `Secure`, and enables HSTS | `production` |
| `AUTH_SECRET_KEY` | **Required in production.** Signs sessions, license keys, CSRF tokens; derives the mailbox-token encryption key | `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `APP_PUBLIC_URL` | Public base URL for reset/invite links and OAuth redirects | `https://copilot.example.com` |
| `ALLOWED_HOSTS` | Host-header allowlist (default `*` = off) | `copilot.example.com` |
| `SIGNUP_ENABLED` | `false` for pure sales-led onboarding (operator provisions via `/operator/*`) | `false` |
| `OPERATOR_TOKEN` | Enables the `/operator/*` sales/ops API (absent = 404) | random token |
| `DEMO_LOGIN_ENABLED` | Advertise + prefill the demo login (auto: on outside production) | `true` |
| `DEMO_SEED_ON_STARTUP` | Seed/reset the demo workspace at boot (shell-less hosts) | `true` |
| `API_AUTH_TOKEN` | When set, benchmark mutating routes + sensitive reads (incl. `/metrics`) require it | random token |
| `CORS_ORIGINS` | Allowed browser origins (default `*`; credentials only ride when pinned) | `https://copilot.example.com` |
| `RATE_LIMIT_PER_MINUTE` | Per-client request cap (default `0` = off) | `120` |
| `LOGIN_RATE_LIMIT_PER_MINUTE` | Sign-in attempt cap per IP and per account (default `0` = off) | `10` |
| `DATABASE_URL` | Postgres for durable data (`postgres://` is normalized automatically) | `postgres://…` |
| `EMAIL_PROVIDER` + `SMTP_*` | Transactional email; default `console` only logs (reset links are not delivered!) | `smtp` |
| `SALES_CONTACT_EMAIL` / `SALES_WEBHOOK_URL` | Where the contact form points / posts new leads | `sales@…` |
| `OPENAI_API_KEY` | LLM provider API key (demo replays cached drafts without one) | `sk-...` |
| `API_BASE_URL` | LLM provider endpoint (OpenAI-compatible) | `https://api.openai.com/v1` |
| `MODEL_NAME` | Model id | `gpt-4o-mini` |
| `AZURE_OPENAI_*` | Native Azure OpenAI settings (endpoint/key/version/deployment) | see `.env.example` |
| `GOOGLE_OAUTH_*` / `MICROSOFT_OAUTH_*` | Real-mailbox OAuth apps — see [docs/OAUTH_SETUP.md](docs/OAUTH_SETUP.md) | — |

When exposing the API to an untrusted network, set `ENVIRONMENT=production`,
`AUTH_SECRET_KEY`, `API_AUTH_TOKEN`, `ALLOWED_HOSTS`, `CORS_ORIGINS`, and the
rate limits. See [SECURITY.md](SECURITY.md).

> **Rotation warning**: rotating `AUTH_SECRET_KEY` invalidates every session,
> every issued license key, and the at-rest encryption of stored mailbox
> OAuth tokens (customers must reconnect). There is no dual-key rotation
> path; treat the secret as precious and back it up.

> **Proxy note**: the container starts uvicorn with `--proxy-headers
> --forwarded-allow-ips="*"` so rate limiting and audit IPs see real clients
> behind a platform proxy (Render, Cloud Run). If you expose the container
> directly to the internet with no proxy, X-Forwarded-For becomes spoofable —
> front it with one.

## Deploy to Render

A [`render.yaml`](render.yaml) Blueprint is included. The container binds the
`$PORT` Render injects automatically (falling back to 8000 locally), so no port
config is required.

1. Push this repo to GitHub.
2. In Render: **New → Blueprint**, select the repo. Render reads `render.yaml`
   and provisions one Docker **web service** (FastAPI, which also serves the
   server-rendered product UI) **plus a Postgres database** wired in via
   `DATABASE_URL`.
3. The blueprint already sets the production posture: `ENVIRONMENT=production`,
   generated `AUTH_SECRET_KEY` / `OPERATOR_TOKEN` / `API_AUTH_TOKEN`, signup
   off, demo login on, demo seeded at boot, rate limits on. Set the `sync:
   false` values (sales email, optional Slack webhook, optional LLM key) in
   the service's **Environment** tab.
4. **Back up the generated `AUTH_SECRET_KEY`** from that tab (see the rotation
   warning above), and note `OPERATOR_TOKEN` — it drives the
   [provisioning runbook](docs/PROVISIONING_RUNBOOK.md).
5. Render health-checks `/health` and serves the app at the assigned URL; the
   product UI is the root (`/` → `/login` → prefilled demo sign-in →
   `/app/inbox`).

⚠ The blueprint's Postgres is `plan: free`, which Render deletes after ~30
days — fine for evaluating, wrong for customers. Upgrade the database plan
(or point `DATABASE_URL` elsewhere) before onboarding anyone real.

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
