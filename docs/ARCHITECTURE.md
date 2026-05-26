# Architecture

A concise map of how the system fits together. For exhaustive detail see
[TECHNICAL_REFERENCE.md](TECHNICAL_REFERENCE.md); for the improvement plan see
[ROADMAP.md](ROADMAP.md).

## Layers

```
                +-------------------------------------------------+
   clients ---> |  FastAPI app (env/api.py)                       |
   (curl,       |  gateway middleware: request-id, rate limit,     |
    dashboard,  |  auth, telemetry  +  global JSON error handler   |
    validator)  +------------------+------------------------------+
                                   |
        +--------------------------+--------------------------+
        |              |               |              |        |
        v              v               v              v        v
   Environment      Grader         Policies/Agents  Persistence  Telemetry
   (env/            (env/          (env/policy,     (env/db,     (telemetry/
   environment.py)  grader.py)     llm_*, agents/)  repositories) metrics,alerts)
        |              |               |              |
        +-----> Tasks/scenarios (env/tasks.py, data_loader.py, data/*.yaml)
                config (env/config.py)  logging (env/logging_config.py)
```

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
| Runtime API | `env/api.py`, `env/dashboard_api.py` | REST + WebSocket surface |
| Simulation | `env/environment.py`, `env/tasks.py`, `env/data_loader.py` | Deterministic inbox state machine + scenarios |
| Scoring | `env/grader.py`, `env/utils.py` | Bounded, monotonic, validator-friendly scores |
| Decisioning | `env/policy.py`, `env/llm_policy.py`, `env/llm_agent.py`, `env/agents/` | Baseline, hybrid planner/executor, LLM, multi-agent |
| Persistence | `env/db.py`, `env/repositories.py`, `env/learning/` | SQLite episodes/preferences + learning store |
| Cross-cutting | `env/config.py`, `env/logging_config.py`, `env/security.py` | Settings, structured logging, auth/rate-limit |
| Observability | `telemetry/` | Prometheus metrics, alert rules |
| Tooling | `baseline/`, `benchmark/`, `reports/` | CLI runners, benchmark matrix, PDF reports |
| UI | `streamlit_app.py`, `dashboard/` | Streamlit console, React dashboard |

## Invariants

- **Validator parity**: `inference.py` log format (`[START]/[STEP]/[END]`) and the
  open-interval `(0,1)` score contract are stable.
- **Determinism**: a given `(task, seed, persona)` always produces the same
  baseline trajectory and score (guarded by `tests/test_grading_rigor.py`).
- **Config is centralized** in `env/config.py`; security and logging are opt-in
  and configured via environment variables (see [.env.example](../.env.example)).

## Notable design decisions

- **Open-interval scoring** (`strict_unit_interval` + `atan` reward squash):
  bounds scores into `(0,1)` while preserving order, to satisfy strict validators.
- **Opt-in security**: the API runs open by default (frictionless local/eval use);
  auth, CORS limits, and rate limiting activate only when configured.
- **HITL approval is opt-in** (`REQUIRE_APPROVAL`): the raw agent acts directly;
  the product path can require human approval for reply/escalate.
