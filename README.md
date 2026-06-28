# Autonomous Executive Email Copilot

![Tests](https://img.shields.io/badge/tests-387%20passing-brightgreen)
![Coverage](https://img.shields.io/badge/coverage-83%25-green)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Lint](https://img.shields.io/badge/lint-ruff-261230)
![License](https://img.shields.io/badge/license-MIT-blue)
![Build](https://img.shields.io/badge/docker-multi--stage-2496ED)

> **A reproducible, deterministic benchmark *and* a production-style product for executive-inbox agents.** A Gym-style reset/step/state environment, bounded and numerically stable scoring, baseline / hybrid / LLM / multi-agent policies, and a full ops stack (FastAPI + React, approvals, telemetry, alerts).

Autonomous Executive Email Copilot is a deterministic, RL-style executive inbox simulation for evaluating agents that triage and manage high-stakes email workloads. It models an executive mailbox with incoming messages, deadlines, business value, risk tags, thread history, and mid-episode interruptions. Agents interact with the environment through a standard reset/step/state loop, choose among classify, prioritize, reply, escalate, and defer actions, and are scored by task-specific graders that keep results bounded and numerically stable (open interval `(0,1)`).

The project is built as a full experimentation stack rather than a single simulator. Scenario generation is driven by YAML task and scenario files; policies include a heuristic baseline, stress-test corruption, LLM-backed decisioning, and hybrid planner/executor modes; and the surrounding tooling supports approvals, replay, benchmark comparison, reports, telemetry, and alerts. A React dashboard provides inbox review, approvals, replay, and team settings on top of the FastAPI service.

## Documentation

- [docs/TECHNICAL_REFERENCE.md](docs/TECHNICAL_REFERENCE.md) — full, code-derived reference.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — layers, request flow, design decisions.
- [docs/BENCHMARK.md](docs/BENCHMARK.md) — benchmark methodology, scoring, and how to reproduce results.
- [docs/WHITEPAPER.md](docs/WHITEPAPER.md) — positioning & methods: what it measures, real results, honest findings.
- [docs/RUNBOOK.md](docs/RUNBOOK.md) — operations: probes, metrics, alerts, incidents.
- [docs/ROADMAP.md](docs/ROADMAP.md) — phased improvement plan and status.
- [CONTRIBUTING.md](CONTRIBUTING.md) · [SECURITY.md](SECURITY.md) · [.env.example](.env.example) — contributing, security policy, configuration.

## What You Can Do

- Simulate executive inbox workloads with deterministic seeds and personas.
- Run baseline, stress, hybrid, and LLM-backed decision policies.
- Score trajectories with bounded, numerically stable grading.
- Compare results across tasks, personas, and seeds.
- Collect approvals, preferences, feedback, replay artifacts, reports, and telemetry.
- Operate via a FastAPI service and a React dashboard.

## Supported Tasks

- `easy_classification`
- `medium_prioritization`
- `hard_full_management`

Task, scenario, and tuning config live in [data/tasks.yaml](data/tasks.yaml), [data/settings.yaml](data/settings.yaml), and [data/scenarios/](data/scenarios/).

## Benchmark Results

Mean task score (open interval `(0,1)`, higher is better) over **3 personas × 3 seeds**
per cell, produced by the reproducible harness. The LLM column is **real Azure OpenAI
`gpt-4o`** (deployment `gpt-4o`, API `2024-12-01-preview`). Full methodology — scoring
weights, the `atan` reward squash, determinism guarantees — is in
[docs/BENCHMARK.md](docs/BENCHMARK.md).

| Task | Baseline (heuristic) | Multi-agent (task-aware) | LLM — Azure `gpt-4o` |
|------|:---:|:---:|:---:|
| `easy_classification` | **1.00** | 0.80 | 0.17 |
| `medium_prioritization` | **1.00** | **1.00** | **1.00** |
| `hard_full_management` | **0.67** | 0.09 | 0.62 |

<sub>Deterministic agents (baseline, multi-agent) have ≈0 variance; the LLM ran at
`temperature=0.2` and averaged ~3k tokens / **≈ $0.009 per episode** (gpt-4o), ~$0.23 for the
full 27-episode LLM sweep. Scores are **persona-invariant** by design — personas shape per-step
reward/penalties, not the headline metric (see [docs/BENCHMARK.md](docs/BENCHMARK.md)).</sub>

**What the numbers say (honest findings, not tuned):**
- The benchmark **discriminates** — a strong heuristic, a naive multi-agent crew, and a frontier LLM separate clearly, and differently per task.
- On the realistic **full-management** task, the LLM (`0.62`) is competitive with the hand-tuned baseline (`0.67`) and far ahead of the naive multi-agent (`0.09`) — this is where model flexibility pays off.
- On narrow **classification**, the LLM scores low (`0.17`): its task-blind safety guardrails (always prioritize first, auto-escalate risk, prefer replies) trade classification coverage for caution. That's an honest **agent-design** finding, not a model-capability one.
- The **multi-agent crew is task-conditioned** (was 0.00 on classification before the coordinator became task-aware — see [docs/BENCHMARK.md](docs/BENCHMARK.md)); the **no-key hybrid** falls back to the strong baseline heuristics, scoring `1.00 / 1.00 / 0.60` with no provider configured (was ~0).

Reproduce (deterministic agents need no API key; add `llm` after configuring a provider):

```bash
python scripts/run_benchmark.py --agents baseline multiagent --out artifacts/results
# writes results.json / results.csv / results.html (aggregated with 95% CIs)
```

## Quick Start

### 1) Install

```bash
python -m venv .venv
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
# Linux/macOS
source .venv/bin/activate

pip install -r requirements.txt
```

### 2) Run API

```bash
uvicorn env.api:app --host 0.0.0.0 --port 8000 --reload
```

### 3) Run React Dashboard (optional)

```bash
cd dashboard
npm install
npm run dev
```

### 4) Tests

```bash
python -m pytest -q
```

## API Surface With Examples

Base URL: `http://localhost:8000`

### 1) Core Runtime

Endpoints:

- `GET /`
- `GET /favicon.ico`
- `GET /health`
- `GET /health/live` (liveness probe)
- `GET /health/ready` (readiness probe — checks DB)
- `GET /version`
- `GET /tasks`
- `POST /reset`
- `POST /step`
- `GET /state`
- `POST /state`

Request:

```bash
curl -s -X POST http://localhost:8000/reset \
  -H "Content-Type: application/json" \
  -d '{"task_id":"easy_classification","seed":42,"persona":"balanced"}'
```

Response (trimmed):

```json
{
  "emails": [
    {
      "id": "msg_001",
      "sender": "client@example.com",
      "priority_hint": "high",
      "risk_tag": "none"
    }
  ],
  "time_remaining": 60,
  "pending_actions": ["classify", "reply", "defer", "escalate", "prioritize"],
  "risk_level": "medium",
  "current_minute": 0,
  "persona": "balanced",
  "remaining_interruptions": 1
}
```

Step action:

```bash
curl -s -X POST http://localhost:8000/step \
  -H "Content-Type: application/json" \
  -d '{"action_type":"classify","email_id":"msg_001","label":"urgent"}'
```

### 2) Scoring And Policy Execution

Endpoints:

- `POST /grader`
- `POST /baseline`
- `POST /leaderboard`
- `GET /replay/{episode_id}`

Baseline run:

```bash
curl -s -X POST http://localhost:8000/baseline \
  -H "Content-Type: application/json" \
  -d '{"task_id":"hard_full_management","seed":42,"persona":"balanced","mode":"baseline","max_steps":100}'
```

Response (trimmed):

```json
{
  "task_id": "hard_full_management",
  "seed": 42,
  "persona": "balanced",
  "mode": "baseline",
  "stress_rate": 0.0,
  "score": 0.732,
  "total_reward": 5.4,
  "steps": 11,
  "breakdown": {
    "classification_accuracy": 0.8,
    "sla": 0.7
  },
  "action_trace": [],
  "decision_trace": []
}
```

Trajectory grading:

```bash
curl -s -X POST http://localhost:8000/grader \
  -H "Content-Type: application/json" \
  -d '{
    "task_id":"easy_classification",
    "seed":42,
    "persona":"balanced",
    "actions":[{"action_type":"classify","email_id":"msg_001","label":"normal"}]
  }'
```

### 3) Approval Workflow

Endpoints:

- `POST /approval/request`
- `POST /approval/{request_id}/approve`
- `POST /approval/{request_id}/reject`
- `GET /approval/{request_id}`
- `GET /approval/pending`
- `GET /approval/history`

Create request:

```bash
curl -s -X POST http://localhost:8000/approval/request \
  -H "Content-Type: application/json" \
  -d '{"action_type":"escalate","email_id":"msg_002","escalate_to":"legal-team"}'
```

Approve request:

```bash
curl -s -X POST http://localhost:8000/approval/REQUEST_ID/approve \
  -H "Content-Type: application/json" \
  -d '{"approver_id":"ops_lead","comment":"Approved for compliance"}'
```

### 4) Episode And Preference Repositories

Endpoints:

- `GET /episodes`
- `GET /episodes/{episode_id}`
- `GET /episodes/stats`
- `GET /preferences/user/{user_id}`
- `PUT /preferences/user/{user_id}`
- `GET /preferences/users`
- `GET /preferences/team/{team_id}`
- `PUT /preferences/team/{team_id}`
- `GET /preferences/teams`

List episodes:

```bash
curl -s "http://localhost:8000/episodes?page=1&limit=2"
```

Response (trimmed):

```json
{
  "episodes": [
    {
      "episode_id": "hard_full_management_42_balanced",
      "task_id": "hard_full_management",
      "score": 0.732
    }
  ],
  "total": 1,
  "page": 1,
  "limit": 2,
  "total_pages": 1
}
```

Save user preference:

```bash
curl -s -X PUT http://localhost:8000/preferences/user/alex \
  -H "Content-Type: application/json" \
  -d '{"default_persona":"strict_ceo","notification_email":"alex@company.com"}'
```

### 5) Learning And Feedback

Endpoints:

- `POST /feedback`
- `GET /feedback`
- `GET /learning/stats`
- `GET /learning/examples/{task_id}/{persona}`

Submit feedback:

```bash
curl -s -X POST http://localhost:8000/feedback \
  -H "Content-Type: application/json" \
  -d '{
    "episode_id":"hard_full_management_42_balanced",
    "task_id":"hard_full_management",
    "seed":42,
    "persona":"balanced",
    "step_index":3,
    "action_type":"reply",
    "email_id":"msg_004",
    "feedback":"good",
    "comment":"Clear and concise response"
  }'
```

Fetch examples:

```bash
curl -s http://localhost:8000/learning/examples/hard_full_management/balanced
```

### 6) Benchmark And Reports

Endpoints:

- `POST /benchmark/run`
- `POST /benchmark/run_html`
- `GET /reports/episode/{episode_id}`
- `POST /reports/generate`

Run benchmark:

```bash
curl -s -X POST http://localhost:8000/benchmark/run \
  -H "Content-Type: application/json" \
  -d '{"tasks":["easy_classification"],"personas":["balanced"],"seeds":[42],"max_steps":50}'
```

Download PDF report:

```bash
curl -L -o report.pdf http://localhost:8000/reports/episode/hard_full_management_42_balanced
```

### 7) Telemetry And Alerting

Endpoints:

- `GET /metrics`
- `POST /alerts/webhook`
- `GET /alerts`

Attach webhook rule:

```bash
curl -s -X POST http://localhost:8000/alerts/webhook \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/webhook","rule_name":"high_failure_rate"}'
```

Response:

```json
{
  "status": "ok",
  "message": "Webhook added to rule high_failure_rate"
}
```

Read metrics:

```bash
curl -s http://localhost:8000/metrics
```

### 8) Dashboard Router

Endpoints:

- `WS /ws/dashboard`
- `GET /dashboard/health`
- `GET /dashboard/state`
- `POST /dashboard/state`
- `POST /dashboard/reset`

Dashboard reset call:

```bash
curl -s -X POST "http://localhost:8000/dashboard/reset?task_id=hard_full_management&seed=42&persona=balanced"
```

WebSocket ping frame:

```json
{"type":"ping"}
```

WebSocket pong frame:

```json
{"type":"pong"}
```

### Auto-Generated Docs

- `GET /docs`

## Modes

- `baseline`: deterministic heuristic agent.
- `stress`: heuristic with randomized perturbation by `stress_rate`.
- `llm`: LLM-driven strategy and action synthesis with safety/approval gates.
- `hybrid`: supported in CLI runner, not accepted by the `/baseline` API schema.

## UI

- React dashboard in [dashboard/src/App.tsx](dashboard/src/App.tsx): inbox, timeline, replay, approvals, team settings, user settings. Built in the Docker image and served at `/dashboard/`.

## Deployment Notes

- API entrypoint export: [main.py](main.py)
- Server launcher: [server/app.py](server/app.py)
- Container build: [Dockerfile](Dockerfile)
- Render Blueprint: [render.yaml](render.yaml)
- CI workflow: [.github/workflows/ci.yml](.github/workflows/ci.yml)
- Deployment guide: [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md)

Run container (multi-stage build also compiles the React dashboard, served at `/dashboard/`):

```bash
docker build -t exec-email-copilot .
docker run -p 7860:7860 exec-email-copilot
# or
docker compose up --build
```

**Deploy on Render:** push to GitHub, then **New → Blueprint** and point Render
at this repo — [`render.yaml`](render.yaml) provisions a single Docker web
service. The container binds the `$PORT` Render injects (7860 locally). See
[DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) for secrets and durable-storage notes.

## Security & Configuration

All configuration is environment-driven (see [.env.example](.env.example), loaded
via `env/config.py`). Security controls are **opt-in** so local dev, tests, and
automated tooling work with zero setup:

- `API_AUTH_TOKEN` — when set, mutating routes require `Authorization: Bearer <token>` or `X-API-Key`.
- `CORS_ORIGINS` — comma-separated allowed origins (default `*`).
- `RATE_LIMIT_PER_MINUTE` — per-IP request cap (default `0` = disabled).
- `REQUIRE_APPROVAL` — gate `reply`/`escalate` behind human approval (default off).
- `LOG_LEVEL` — structured logs; every response carries an `X-Request-ID`.

Observability: Prometheus metrics at `/metrics`, alert evaluation at `/alerts`,
provisioning under [telemetry/](telemetry/), and an ops [runbook](docs/RUNBOOK.md).

## Testing Coverage

**387 tests pass at 83% coverage.** Tests under [tests/](tests/) cover API contracts, determinism, grading bounds, approvals, LLM behavior, benchmark/report generation, telemetry, dashboard routes, and the score/log-format contracts — plus a Hypothesis-driven property/invariant harness ([tests/harness/](tests/harness/)). Run the full CI gate locally with `make cov`.

## Important Constraints

- `/baseline` mode enum is `baseline | stress | llm`.
- `/baseline` runs are persisted to the episode DB and (when they clear the score threshold) auto-saved to the learning trajectory store; `/replay/{episode_id}` falls back to the DB so replay survives a restart.
- LLM mode behavior depends on provider credentials and guardrail checks. The human-in-the-loop approval gate is opt-in (`LLMAgent(require_approval=True)` or the `REQUIRE_APPROVAL` env var); with it off the agent returns its decided action directly.
- LLM responses are cached by observation hash (TTL + size cap); the cache is bypassed when approval is required.