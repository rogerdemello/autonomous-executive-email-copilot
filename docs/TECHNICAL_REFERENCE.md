# Autonomous Executive Email Copilot

Autonomous Executive Email Copilot is a data-driven executive inbox simulation environment with:

- deterministic scenarios and grading
- multiple decision policies (baseline, stress, LLM, hybrid planner/executor, multi-agent benchmark mode)
- FastAPI runtime endpoints exposing an RL-style reset/step/state loop
- a React dashboard
- benchmark, comparison, report generation, telemetry, and approval workflows

This README is a full, code-derived functionality reference.

## 1) What This Project Implements

### Core simulation capabilities

- Executive inbox environment with realistic email attributes (deadline, business value, risk tag, thread history)
- Action space: classify, prioritize, reply, escalate, defer
- Mid-episode interruptions (new incoming emails)
- Persona-aware penalty shaping: strict_ceo, balanced, chill_manager
- Risk-aware penalties for unresolved urgent/critical items
- Deterministic task generation from YAML + seed

### Policy and decision capabilities

- Heuristic baseline policy
- Stress mode (intentionally corrupt baseline actions by configurable rate)
- LLM mode with safety checks, guardrails, fallback, token/cost tracking, and approval gating
- Hybrid mode (LLM strategic planner + deterministic executor)
- Multi-agent system for benchmark experiments (coordinator, classifier, responder, escalator)

### Product and ops capabilities

- Full REST API for runtime, grading, baselines, leaderboard, episodes, approvals, feedback, learning stats, benchmark, reports, telemetry, alerts
- Dashboard API + WebSocket push channel
- React dashboard app
- SQLite-backed repositories for episodes/preferences/team settings and learning feedback
- PDF episode reporting
- Prometheus-style metrics output and alert rules

## 2) Architecture

### Runtime layers

1. Environment core (`env/environment.py`)
2. Task/scenario loader (`env/tasks.py`, `env/data_loader.py`, `data/*.yaml`)
3. Grader (`env/grader.py`)
4. Policies/agents (`env/policy.py`, `env/llm_policy.py`, `env/llm_agent.py`, `env/agents/*`)
5. API layer (`env/api.py`, `env/dashboard_api.py`)
6. UI layer (`dashboard/src/*`)
7. Persistence and learning (`env/db.py`, `env/repositories.py`, `env/learning/*`)
8. Telemetry and alerts (`telemetry/*`)
9. Benchmark and reports (`benchmark/*`, `baseline/*`, `reports/*`)

### Data source model

All scenario and tuning content is YAML-driven:

- `data/tasks.yaml`
- `data/settings.yaml`
- `data/scenarios/easy_classification.yaml`
- `data/scenarios/medium_prioritization.yaml`
- `data/scenarios/hard_full_management.yaml`

No scenario payloads are hardcoded in environment logic.

## 3) Runtime Entrypoints and Commands

### API server

```bash
uvicorn env.api:app --host 0.0.0.0 --port 8000 --reload
```

### Container server entrypoint

```bash
python -m server.app
```

Runs `uvicorn server.app:app --host 0.0.0.0 --port 7860`.

### Alternative ASGI import entrypoint

`main.py` exports `app` from `env.api`.

### React dashboard

```bash
cd dashboard && npm install && npm run dev
```

In the container build the dashboard is compiled and served at `/dashboard/`.

### Baseline runner CLI

```bash
python -m baseline.run_baseline --task hard_full_management --seed 42 --persona balanced --mode baseline
python -m baseline.run_baseline --task hard_full_management --seed 42 --persona balanced --mode stress --stress-rate 0.5
python -m baseline.run_baseline --task hard_full_management --seed 42 --persona balanced --mode llm
python -m baseline.run_baseline --task hard_full_management --seed 42 --persona balanced --mode hybrid --planner-interval 3
```

### Leaderboard CLI

```bash
python -m baseline.leaderboard --tasks easy_classification,medium_prioritization,hard_full_management --personas strict_ceo,balanced,chill_manager --seeds 42,43,44
```

### Inference script

```bash
python inference.py
python inference.py --task hard_full_management --max-steps 120
```

## 4) Environment Model

### Observation schema

Observation includes:

- `emails`: list of inbox items (id, sender, role, subject, body, priority_hint, deadline_minutes, business_value, risk_tag, thread_history)
- `time_remaining`
- `pending_actions`
- `risk_level`
- `current_minute`
- `persona`
- `remaining_interruptions`

### Action schema

- `action_type`: `classify | reply | defer | escalate | prioritize`
- `email_id` (when applicable)
- `label` for classify (`spam | normal | urgent`)
- `content` for reply
- `priority_order` for prioritize
- `escalate_to` for escalate

### Step lifecycle

Each step:

1. Advances time by action cost from `data/settings.yaml`
2. Decrements unresolved email deadlines
3. Injects interruptions whose trigger minute has been reached
4. Applies action-specific reward/penalty
5. Applies deadline penalties for unresolved urgent items that hit deadline
6. Clips step reward to `[-1, 1]`
7. On terminal step, applies unresolved urgent/critical terminal penalties

### Done conditions

Episode ends when either:

- `time_remaining <= 0`, or
- no pending interruptions remain and no pending actions remain

### Action rewards and penalties

- `classify`: +0.2 if correct, 0 otherwise; repeat or malformed classify gets redundancy penalty
- `prioritize`: +`0.3 * ranking_similarity`
- `reply`: +`0.5 * reply_keyword_score`; wrong reply on critical expected non-reply can penalize heavily
- `escalate`: +0.4 if expected escalate, +0.1 extra if target matches recommended escalation
- `defer`: +0.1 only when defer is expected; deferring urgent emails incurs strong penalty
- unsupported/malformed actions: redundancy/error penalty paths

Persona multipliers from `data/settings.yaml` scale deadline, terminal, urgent-defer, and redundant penalties.

## 5) Data Loading and Difficulty Scaling

Scenario creation in `env/tasks.py` and `env/data_loader.py` supports:

- seeded deterministic shuffle of initial emails
- deterministic interruption trigger resolution from fixed ranges
- synthetic difficulty scaling

Scaling behavior:

- easy: no extra scaling pressure
- medium: time budget reduced to 92% of base, conflicting deadline metadata added
- hard: time budget reduced to 85%, adversarial wording mutations may be applied, conflicting deadlines and extra interruptions may be synthesized

YAML is cached by mtime+size and supports hot reload when files are modified.

## 6) Grading and Score Semantics

`env/grader.py` computes task scores from environment metrics:

- easy_classification: `classification_accuracy`
- medium_prioritization: `prioritization`
- hard_full_management:

```text
0.3 * classification_accuracy
+ 0.3 * action_correctness
+ 0.4 * response_quality
```

Breakdown metrics include:

- `classification_accuracy`
- `action_correctness`
- `response_quality`
- `prioritization`
- `resolved_ratio`

Important bounds behavior:

- all scores and breakdown metrics are passed through `strict_unit_interval`, producing open interval values `(0, 1)`
- `total_reward` in grader output is normalized through `0.5 + atan(total_reward) / pi`

This keeps scores numerically stable: downstream consumers never have to special-case exact 0 or 1.

## 7) Policy and Agent Modes

### Baseline policy (`env/policy.py`)

- first action is prioritize
- classifies all emails via heuristic terms and risk/priority cues
- escalates legal/security urgent items
- replies to urgent non-legal/non-security items
- defers lower-priority items
- emits keepalive prioritize actions while interruptions are still pending

### Stress mode (`baseline/run_baseline.py`)

With probability `stress_rate`, baseline actions are corrupted:

- classify labels flipped to a different label
- priority order reversed
- reply converted to defer
- escalate target changed to `finance_lead`

### LLM agent mode (`env/llm_agent.py`)

Implemented features:

- first action forced to prioritize
- automatic escalation for legal/security risk emails
- JSON-only parsing/validation of model output
- safety checks:
  - prompt injection regex detection
  - risky reply content detection
  - forbidden escalation target detection
- dynamic model retry (`MODEL_NAME` then `LARGER_MODEL` when needed)
- fallback statuses: timeout/parse/validation/provider
- token usage and cost estimation
- approval queue integration for `reply` and `escalate`

### Hybrid planner/executor (`env/policy.py` + `env/llm_policy.py`)

- planner selects high-level strategy every N steps (`planner_interval`)
- executor converts strategy to concrete actions with deterministic fallback
- strategies:
  - `prioritize_urgent`
  - `batch_reply`
  - `escalate_critical`
  - `defer_low_value`
  - `monitor`

### Multi-agent coordination (`env/agents/*`)

- `CoordinatorAgent` delegates to specialist agents
- `ClassifierAgent`, `ResponderAgent`, `EscalatorAgent`
- conflict resolution by fixed priority ordering
- communication log support for inter-agent traceability

## 8) Complete API Surface

### Core runtime

- `GET /` - HTML landing page
- `GET /favicon.ico` - empty 204 response
- `GET /health`
- `GET /tasks`
- `POST /reset`
- `POST /step`
- `GET /state`
- `POST /state`

### Scoring and policy execution

- `POST /grader`
- `POST /baseline`
- `POST /leaderboard`
- `GET /replay/{episode_id}`

### Approval workflow

- `POST /approval/request`
- `POST /approval/{request_id}/approve`
- `POST /approval/{request_id}/reject`
- `GET /approval/{request_id}`
- `GET /approval/pending`
- `GET /approval/history`

### Episode and preference repositories

- `GET /episodes`
- `GET /episodes/{episode_id}`
- `GET /episodes/stats`
- `GET /preferences/user/{user_id}`
- `PUT /preferences/user/{user_id}`
- `GET /preferences/users`
- `GET /preferences/team/{team_id}`
- `PUT /preferences/team/{team_id}`
- `GET /preferences/teams`

### Learning and feedback

- `POST /feedback`
- `GET /feedback`
- `GET /learning/stats`
- `GET /learning/examples/{task_id}/{persona}`

### Benchmark and reports

- `POST /benchmark/run`
- `POST /benchmark/run_html`
- `GET /reports/episode/{episode_id}`
- `POST /reports/generate`

### Telemetry and alerting

- `GET /metrics`
- `POST /alerts/webhook`
- `GET /alerts`

### Dashboard-specific router

- `WS /ws/dashboard`
- `GET /dashboard/health`
- `GET /dashboard/state`
- `POST /dashboard/state`
- `POST /dashboard/reset`

### Auto-generated docs

- `GET /docs` (FastAPI OpenAPI UI)

## 9) Dashboard and UI Functionality

### React dashboard (`dashboard/`)

Views (`dashboard/src/components/*`):

- Inbox
- Timeline
- Replay
- Approval Queue
- Team settings
- User settings

The dashboard talks to the FastAPI service via the shared API client
(`dashboard/src/api.ts`, with timeout/retry/typed errors) and a live WebSocket
(`useDashboardSocket`, ping/pong + reconnect). In the container build it is
compiled and served at `/dashboard/`.

### React dashboard (`dashboard/src/*`)

Main tabs/components:

- Inbox
- Timeline
- Replay
- Approvals
- Team
- Settings

React app features:

- periodic `/health` connectivity check
- configurable API base
- inbox reset/state fetch and email list rendering
- timeline rendering from `decision_trace`
- replay controls and step navigation
- approval pending/history actions
- user/team preference editing

Build/deploy notes:

- Vite base path is `/dashboard/`
- FastAPI serves static dashboard files when `dashboard/dist` exists

## 10) Persistence and Learning Stores

### Primary SQLite (`data/episodes.db`)

Tables in `env/db.py`:

- `episodes`
- `decisions`
- `user_preferences`
- `team_settings`

Repository layer in `env/repositories.py` supports CRUD-like list/filter stats for episodes and preferences.

### Learning SQLite (`env/data/trajectories.db`)

Tables in `env/learning/trajectory_store.py`:

- `successful_trajectories` (score-thresholded stores)
- `user_feedback`

Learning utilities:

- example extraction by action type
- prompt enhancement using few-shot snippets from stored trajectories

## 11) Benchmarking and Reporting

### Benchmark subsystem (`benchmark/*`)

- agents: baseline, llm, multiagent
- runner: Cartesian runs across tasks/personas/seeds
- reporter: HTML and JSON summaries with aggregate averages

### Leaderboard subsystem (`baseline/leaderboard.py`)

For each task/persona across seeds, computes:

- avg/min/max score
- avg reward
- avg steps
- 95% confidence interval margin (t-distribution approximation)
- failure rate percentage below score threshold
- fairness score derived from cross-persona score variance

Optional CSV output path supported.

### PDF reporting (`reports/generator.py`)

Generates PDF bytes with sections:

- episode overview
- score breakdown
- action timeline
- telemetry timestamps

## 12) Telemetry and Alerting

### Metrics (`telemetry/metrics.py`)

In-memory metrics primitives:

- counter
- gauge
- histogram

Output format is Prometheus-like text and exposed through `/metrics`.

### Alerts (`telemetry/alerts.py`)

Rule engine with default rules:

- high failure rate
- high API error rate
- cost spike

Supports webhook POST dispatch for triggered alerts.

### Grafana artifact

`telemetry/grafana_dashboard.json` contains a dashboard and rule templates.

## 13) Inference Script Behavior

`inference.py` supports:

- running one or all default tasks
- OpenAI/Azure-compatible base URL normalization
- fallback to deterministic baseline policy when provider/key unavailable
- exact log format:

```text
[START] task=... env=... model=...
[STEP] step=... action=... reward=... done=... error=...
[END] success=... steps=... score=... rewards=...
```

## 14) Environment Variables

### LLM/runtime variables

- `OPENAI_API_KEY`
- `HF_TOKEN`
- `API_BASE_URL`
- `MODEL_NAME`
- `LARGER_MODEL`
- `CONFIDENCE_THRESHOLD`
- `AZURE_API_VERSION`

### Dashboard variable

- `APP_API_BASE_URL` (dashboard-to-app API URL)

## 15) Complete Source Module Map

### Root runtime files

- `main.py`: ASGI app export
- `server/app.py`: uvicorn launcher on port 7860
- `inference.py`: CLI inference runner (structured `[START]/[STEP]/[END]` logs)
- `Dockerfile`: container build/runtime

### `env/` package

- `env/api.py`: primary FastAPI app with all REST endpoints
- `env/dashboard_api.py`: dashboard endpoints + WebSocket broadcast loop
- `env/environment.py`: simulation state machine
- `env/models.py`: Pydantic models and API schemas
- `env/tasks.py`: task and scenario assembly
- `env/data_loader.py`: YAML loader/cache + synthetic difficulty combinators
- `env/utils.py`: scoring helpers, heuristics, persona profiles, ranking
- `env/grader.py`: trajectory evaluator
- `env/policy.py`: baseline policy, executor, hybrid policy
- `env/llm_policy.py`: strategic planner and LLMPolicy wrapper
- `env/llm_agent.py`: LLM decision engine, safety, fallback, approvals
- `env/approval.py`: approval request lifecycle store
- `env/db.py`: SQLAlchemy models and DB bootstrap
- `env/repositories.py`: episode/preferences/team repositories

### `env/agents/`

- `base.py`: base abstract agent contract
- `classifier.py`: classification specialist
- `responder.py`: reply specialist
- `escalator.py`: escalation specialist
- `coordinator.py`: delegation and conflict resolution
- `__init__.py`: exports multi-agent API

### `env/learning/`

- `trajectory_store.py`: successful trajectory + feedback persistence
- `example_extractor.py`: few-shot extraction by action type
- `prompt_enhancer.py`: appends few-shot examples to prompts

### `baseline/`

- `run_baseline.py`: mode runner (baseline/stress/llm/hybrid)
- `leaderboard.py`: aggregate performance summaries and CSV export

### `benchmark/`

- `agents.py`: benchmark execution adapters
- `runner.py`: run matrix coordinator
- `reporter.py`: JSON/HTML benchmark output

### `reports/`

- `generator.py`: PDF report generation

### `telemetry/`

- `metrics.py`: Prometheus-style in-memory metrics
- `alerts.py`: alert rules and webhook dispatch
- `grafana_dashboard.json`: dashboard/rule definitions

### `dashboard/src/`

- `App.tsx`: shell and tab routing
- `components/Inbox.tsx`: reset/state + inbox display
- `components/Timeline.tsx`: LLM decision timeline viewer
- `components/Replay.tsx`: replay controls and stepwise inspection
- `components/ApprovalQueue.tsx`: pending/history approval actions
- `components/Settings.tsx`: user preference management UI
- `components/Team.tsx`: team rules and escalation target UI
- `index.css`: UI styling
- `main.tsx`: React mount

### Tests (`tests/`)

- `test_api_contract.py`: runtime and API contract checks
- `test_ai_smoke.py`: CLI/API/UI AI smoke and fallback paths
- `test_approval.py`: approval workflow behavior
- `test_benchmark.py`: benchmark agent/runner/reporter behavior
- `test_dashboard.py`: dashboard API route coverage
- `test_env_determinism.py`: deterministic baseline with same seed
- `test_grader_bounds.py`: open interval score/breakdown behavior
- `test_hot_reload.py`: YAML hot reload behavior
- `test_interruptions_and_leaderboard.py`: interruption and leaderboard assertions
- `test_llm_agent.py`: LLM agent parse/fallback/guardrail behavior
- `test_llm_benchmark.py`: LLM benchmark behavior across personas
- `test_reports.py`: PDF/report endpoint behavior
- `test_safety.py`: safety regex and blocking paths
- `test_telemetry.py`: metrics and alert rules
- `test_score_contract.py`: score/log-format contract checks

## 16) Local Setup

### Python environment (recommended)

```bash
python -m venv .venv
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
# Linux/macOS
source .venv/bin/activate

pip install -r requirements.txt
```

### Start API

```bash
uvicorn env.api:app --host 0.0.0.0 --port 8000 --reload
```

### Start React dashboard (dev)

```bash
cd dashboard
npm install
npm run dev
```

### Build React dashboard for FastAPI static serving

```bash
cd dashboard
npm run build
```

### Run tests

```bash
python -m pytest -q
```

## 17) Docker

```bash
docker build -t exec-email-copilot .
docker run -p 7860:7860 exec-email-copilot
```

Container healthcheck probes `http://localhost:7860/health`.

## 18) CI and Deployment Files

- `.github/workflows/ci.yml` defines lint/test/typecheck/security/frontend/docker/inference jobs
- `DEPLOYMENT_GUIDE.md` covers container and cloud deployment

## 19) Operational Notes and Current Constraints

1. API `/baseline` accepts modes `baseline`, `stress`, `llm` (Pydantic `PolicyMode`).
2. CLI `baseline.run_baseline` supports an additional `hybrid` mode.
3. The `/baseline` endpoint persists each run via `EpisodeRepository.save_episode` (in addition to the in-memory `episode_history_store`), and `/replay/{episode_id}` falls back to the DB when the in-memory entry is absent.
4. Successful trajectory auto-save is wired into the baseline flow: above-threshold runs are written to the learning trajectory store; feedback endpoints also write to the learning DB.
5. Telemetry is instrumented via an HTTP middleware that records per-request count/latency and API errors, plus episode start/end around `/baseline`, so `/metrics` reflects real traffic.
6. LLM mode typically requires provider credentials for sustained non-guardrail steps.
7. `LLMAgent` caches responses by observation hash (TTL + size cap) and reuses them across calls; the cache is skipped when `require_approval` is enabled so it cannot bypass the approval gate.
8. The human-in-the-loop approval gate for `reply`/`escalate` is opt-in (`LLMAgent(require_approval=True)` or the `REQUIRE_APPROVAL` env var); off by default.
9. Dashboard WebSocket action handling passes raw payload action directly into env step path; clients should align with expected action object schema.

## 20) Supported Tasks

- `easy_classification`
- `medium_prioritization`
- `hard_full_management`

Task metadata and behavior tuning are fully configurable in YAML files under `data/`.
