# API and configuration reference

The benchmark simulator's HTTP surface, the runtime modes, and the security and
configuration switches. This used to be most of the README; it is reference material,
and the README is about the product.

For the **product's** own JSON API — `auth`, `organization`, `mailbox`, `inbox`,
`billing` — open the interactive docs at `/docs` (every endpoint is grouped by tag),
or read [COMMERCIAL.md](COMMERCIAL.md). Everything below is the simulator, plus the
configuration that applies to the whole server.

---

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

### 8) Simulator live-state API

A WebSocket + REST view of the running simulator (the React dashboard that
once consumed it was removed; the API remains for external tooling):

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
- `hybrid`: LLM planner + heuristic executor; accepted by both the CLI runner and the `/baseline` API.

## Security & Configuration

All configuration is environment-driven (see [.env.example](../.env.example), loaded
via `app/core/config.py`). Security controls are **opt-in** so local dev, tests, and
automated tooling work with zero setup:

- `API_AUTH_TOKEN` — when set, mutating routes **and reads of the benchmark
  surface** (`/approval`, `/episodes`, `/preferences`, `/dashboard`, …) require
  `Authorization: Bearer <token>` or `X-API-Key`. The product API and web UI
  authenticate per-user and are unaffected.
- `CORS_ORIGINS` — comma-separated allowed origins (default `*`).
- `RATE_LIMIT_PER_MINUTE` — per-IP request cap (default `0` = disabled).
- `REQUIRE_APPROVAL` — **benchmark simulator only**: routes the sim agent's
  `reply`/`escalate` through the in-memory approval store (default off). The
  *product's* approval gate is not a setting — outbound actions from a real
  mailbox are always held for a human (`app/copilot/pipeline.py`).
- `LOG_LEVEL` — structured logs; every response carries an `X-Request-ID`.
- `ENVIRONMENT=production` — refuses to start without `AUTH_SECRET_KEY`, rather than
  silently signing sessions and licenses with the well-known development secret.
- `OIDC_ISSUER` / `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` — enables SSO sign-in;
  id_tokens are verified RS256 against the issuer's published JWKS.

The web session is an HttpOnly, `SameSite=Lax` cookie carrying the same token the API
accepts as a Bearer header, and every mutating form is guarded by a signed CSRF token
bound to that session.

Observability: Prometheus metrics at `/metrics`, alert evaluation at `/alerts`,
provisioning under [telemetry/](../telemetry/), and an ops [runbook](RUNBOOK.md).

## Important Constraints

- `/baseline` mode enum is `baseline | stress | llm | hybrid`.
- `/baseline` runs are persisted to the episode DB and (when they clear the score threshold) auto-saved to the learning trajectory store; `/replay/{episode_id}` falls back to the DB so replay survives a restart.
- LLM mode behavior depends on provider credentials and guardrail checks. The human-in-the-loop approval gate is opt-in (`LLMAgent(require_approval=True)` or the `REQUIRE_APPROVAL` env var); with it off the agent returns its decided action directly.
- LLM responses are cached by observation hash (TTL + size cap); the cache is bypassed when approval is required.
