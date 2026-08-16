# Architecture

A concise map of how the system fits together. For exhaustive detail see
[TECHNICAL_REFERENCE.md](TECHNICAL_REFERENCE.md); for the improvement plan see
[ROADMAP.md](ROADMAP.md).

## Layers

```
                 +------------------------------------------------+
   browsers  --> |  FastAPI app (app/main.py)                     |
   API clients   |  gateway middleware: request-id, rate limit,   |
   tooling       |  auth, telemetry + global JSON error handler   |
                 +-----------------------+------------------------+
                                         |
        +----------------- PRODUCT ------+------ RESEARCH ---------+
        |                 |              |            |            |
        v                 v              v            v            v
   Web UI            Copilot        SaaS layer   Simulation     Telemetry
   (app/web:         (app/copilot:  (app/saas:   (research/sim: (telemetry/
   templates,        providers,     auth, RBAC,  environment,   metrics,
   session, CSRF)    enrich,        licensing,   grader,        alerts)
                     policy)        sync, audit) agents)
        |                 |              |            |
        +--------> config (app/core/config.py) · models · db · security
                   scenarios (data/*.yaml) · demo mailbox (data/demo/)
```

Dependencies point one way: `research` may import from `app`, never the reverse.
Two modules are shared deliberately — `app/core/models.py` and
`app/copilot/policy.py` are used by both the product pipeline and the benchmark,
so they live on the product side and the benchmark imports up into them.

## Request flow (e.g. `POST /baseline`)

1. **Gateway middleware** assigns a request id (`X-Request-ID`), enforces opt-in
   rate limit + auth, and times the request for `/metrics`.
2. The handler runs a **policy** (`baseline`/`stress`/`llm`/`hybrid`) against the
   **environment**, scoring each step with the **grader** (bounded to the open
   interval `(0,1)`).
3. The result is **persisted** (episode DB + above-threshold learning trajectory)
   best-effort, and returned. `/replay` reads memory then falls back to the DB.

## Key components

| Area | Module(s) | Responsibility |
|------|-----------|----------------|
| Runtime API | `app/main.py`, `app/live_api.py` | REST + WebSocket surface |
| Simulation | `research/sim/environment.py`, `research/sim/tasks.py`, `research/sim/data_loader.py` | Deterministic inbox state machine + scenarios |
| Scoring | `research/sim/grader.py`, `app/core/utils.py` | Bounded, monotonic, numerically stable scores |
| Decisioning | `app/copilot/policy.py`, `app/llm/policy.py`, `app/llm/agent.py`, `research/sim/agents/` | Baseline, hybrid planner/executor, LLM, multi-agent |
| Persistence | `app/core/db.py`, `app/core/repositories.py`, `research/sim/learning/` | SQLite episodes/preferences + learning store |
| Cross-cutting | `app/core/config.py`, `app/core/logging_config.py`, `app/core/security.py` | Settings, structured logging, auth/rate-limit |
| Observability | `telemetry/` | Prometheus metrics, alert rules |
| Tooling | `research/baseline/`, `research/benchmark/`, `reports/` | CLI runners, benchmark matrix, PDF reports |
| Product | `app/copilot/`, `app/saas/` | Mail providers, signal inference, tenant sync, approvals |
| UI | `app/web/` | Server-rendered Jinja templates + one stylesheet; no bundler |

## Invariants

- **Score/log contract**: `research/inference.py` log format (`[START]/[STEP]/[END]`) and the
  open-interval `(0,1)` score contract are stable so tooling can parse runs reliably.
- **Determinism**: a given `(task, seed, persona)` always produces the same
  baseline trajectory and score (guarded by `tests/test_grading_rigor.py`).
- **Config is centralized** in `app/core/config.py`; security and logging are opt-in
  and configured via environment variables (see [.env.example](../.env.example)).

## Notable design decisions

- **Open-interval scoring** (`strict_unit_interval` + `atan` reward squash):
  bounds scores into `(0,1)` while preserving order, so downstream consumers never
  have to special-case exact `0.0`/`1.0`.
- **Opt-in security**: the API runs open by default (frictionless local/eval use);
  auth, CORS limits, and rate limiting activate only when configured.
- **HITL approval is opt-in** (`REQUIRE_APPROVAL`): the raw agent acts directly;
  the product path can require human approval for reply/escalate.
