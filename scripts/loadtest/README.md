# Load testing

A [Locust](https://locust.io/) load test for the Autonomous Executive Email
Copilot API. It exercises the hot read paths and a representative mutating flow
so you can measure latency and throughput before a deploy or a capacity change.

Locust is an **optional development dependency** and is deliberately *not* in
`requirements.txt` (it pulls in a web UI and async stack the runtime does not
need). Install it on demand.

## Install

```bash
pip install locust
```

## Run the server under test

In one terminal, start the API the usual way (see the README quickstart):

```bash
uvicorn env.api:app --host 0.0.0.0 --port 8000
```

The same app serves on container port `7860` in Docker; point `--host` at
whatever address/port the server is actually listening on.

## Run the load test

From the repository root:

```bash
# Interactive web UI on http://localhost:8089
locust -f scripts/loadtest/locustfile.py --host http://localhost:8000
```

Open <http://localhost:8089>, set the number of users and spawn rate, and start
the run. For an unattended (headless) run with a fixed shape and duration:

```bash
locust -f scripts/loadtest/locustfile.py --host http://localhost:8000 \
  --headless --users 50 --spawn-rate 10 --run-time 2m
```

- `--users` — peak number of concurrent simulated clients.
- `--spawn-rate` — how many users to add per second until the peak is reached.
- `--run-time` — total duration (e.g. `30s`, `2m`, `1h`).

Against a non-local target, set `--host` accordingly, e.g.
`--host http://localhost:7860` (Docker) or a remote base URL.

## What it does

Each simulated user (`EmailCopilotUser`) waits 0.5–2.0s between tasks and picks
a task by weight:

| Task | Endpoint(s) | Weight | Notes |
|------|-------------|-------:|-------|
| `health` | `GET /health` | 10 | Cheapest path; mirrors the healthcheck/probes. |
| `tasks` | `GET /tasks` | 5 | Task metadata + JSON schemas. |
| `state` | `GET /state` | 5 | Current environment snapshot. |
| `metrics` | `GET /metrics` | 3 | Prometheus text exposition. |
| `reset_then_step` | `POST /reset`, `POST /step` | 2 | Representative mutating flow. |

On start, each user resets the environment so `/state` and `/step` have an
episode to act on. The mutating flow resets, reads the returned observation, and
classifies the first email (falling back to a `prioritize` action when no emails
remain). The read paths are weighted far above the writes because that matches
real traffic (probes, scrapers, dashboards) and keeps SQLite — the zero-config
default backend — from being the dominant factor in the numbers.

> Note: if the server has `API_AUTH_TOKEN` set, the mutating routes (`/reset`,
> `/step`) require a token. Either run the load test against a server with auth
> disabled, or extend the requests in `locustfile.py` to send an
> `Authorization: Bearer <token>` header. Likewise, leave
> `RATE_LIMIT_PER_MINUTE` at `0` (the default) so the limiter does not skew
> results.

## Reading the results

- **Web UI** — the *Statistics* tab shows, per named request, the request count,
  failure count, median / 95th / 99th percentile latency, and current RPS. The
  *Charts* tab plots RPS, response times, and user count over time. The
  *Failures* tab lists any non-2xx/transport errors with a reason.
- **Headless** — Locust prints an aggregated table on exit. Add
  `--csv results/loadtest` to also write
  `results/loadtest_stats.csv`, `_stats_history.csv`, and `_failures.csv` for
  later analysis or CI artifacts.

Focus on the **p95 / p99 latency** columns (tail latency, what users feel) and
the **failure rate**, not just the mean. A rising p99 while RPS is flat usually
means you have found a bottleneck (often the SQLite write path under the
`/reset` + `/step` flow).

## Suggested target SLOs

These are starting points for a single-instance deployment on modest hardware;
tune them to your environment and re-baseline after infra changes.

| Metric | Target |
|--------|--------|
| `GET /health` p99 latency | < 20 ms |
| `GET /tasks`, `GET /state` p95 latency | < 100 ms |
| `GET /metrics` p95 latency | < 150 ms |
| `POST /reset` + `POST /step` p95 latency | < 400 ms |
| Error rate (all endpoints) | < 0.1% |
| Sustained throughput before p95 SLOs breach | record as the capacity number |

A useful capacity exercise: ramp `--users` until any p95 SLO is breached or the
error rate climbs above the threshold, then record the RPS just below that point
as the safe sustained throughput for one instance.
