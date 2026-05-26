# Operations Runbook

Operational reference for running the Autonomous Executive Email Copilot API.

## Health & probes

| Endpoint | Purpose | Healthy response |
|----------|---------|------------------|
| `GET /health` | Simple liveness (back-compat) | `200 {"status":"ok"}` |
| `GET /health/live` | Liveness — process is up, no dependencies | `200 {"status":"alive"}` |
| `GET /health/ready` | Readiness — database reachable | `200 {"status":"ready"}` / `503 {"status":"not_ready"}` |

- **Kubernetes**: use `/health/live` for the liveness probe and `/health/ready` for the readiness probe.
- The container `HEALTHCHECK` calls `/health`.
- Graceful shutdown: uvicorn turns `SIGTERM` into a lifespan shutdown (logged); in-flight requests drain.

## Metrics

- `GET /metrics` returns Prometheus text. Key series: `requests_total`,
  `request_duration_ms_*`, `api_errors_total`, `episodes_completed_total`,
  `episodes_failed_total`, `active_episodes`.
- Scrape config: [`telemetry/prometheus.yml`](../telemetry/prometheus.yml).
- Local stack: `docker compose -f telemetry/docker-compose.observability.yml up`
  (Prometheus on :9090, Grafana on :3001 with the bundled
  [dashboard](../telemetry/grafana_dashboard.json) + provisioned datasource).

## Alerts

- `GET /alerts` evaluates the default rules against current metrics and returns
  `active_alerts` + `all_alerts`. Default rules: **high failure rate**, **high API
  error rate**, **cost spike**.
- Attach a webhook: `POST /alerts/webhook {"url": "...", "rule_name": "high_failure_rate"}`.
  Only `http(s)` webhook URLs are accepted.

## Logs & correlation

- Structured logs go to stdout at `LOG_LEVEL` (default `INFO`). Each line carries
  `[req=<id>]`. Every response echoes `X-Request-ID`; clients may send their own.

## Common incidents

| Symptom | Likely cause | Action |
|---------|--------------|--------|
| `/health/ready` 503 | DB file unwritable / volume issue | Check the container's `/app/data` mount and permissions (runs as non-root `appuser`). |
| `episodes_failed_total` climbing | Bad task/seed input or env error | Inspect logs by `X-Request-ID`; check `/baseline` payloads. |
| `api_errors_total{type="unauthorized"}` spikes | Clients missing token after `API_AUTH_TOKEN` was set | Distribute the token or rotate it. |
| 429 responses | Rate limit too low | Raise `RATE_LIMIT_PER_MINUTE` or set `0` to disable. |
| LLM mode degraded | Missing/invalid provider key | Set `OPENAI_API_KEY`/`HF_TOKEN`; the agent otherwise falls back to baseline. |

## Security toggles (env)

- `API_AUTH_TOKEN` — require a bearer/`X-API-Key` token on mutating routes.
- `CORS_ORIGINS` — comma-separated allowed origins (default `*`).
- `RATE_LIMIT_PER_MINUTE` — per-IP cap (default `0` = off).

See [.env.example](../.env.example) for the full list.
