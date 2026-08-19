# Roadmap: Autonomous Executive Email Copilot → World-Class

**Scope:** Comprehensive (eval-benchmark + production product + flagship OSS), all priority areas, **correctness first**.

**Guiding principle:** *Make every claim in the docs true and tested before adding anything new.* The project already has impressive surface area; world-class means the depth matches the surface.

## Invariants (must not break)
- Score/log contract: `inference.py` log format (`[START]/[STEP]/[END]`) and the open-interval `(0,1)` score contract.
- Deterministic baseline given `(task, seed, persona)`.
- No breaking changes to existing API response shapes without versioning (`/v1`).

---

## Phase 0 — Make it honest & green (correctness foundation) ✅ COMPLETE
Bar: `pytest` 100% green, zero deprecation warnings, every doc claim backed by a test.

- [x] Resolve HITL/agent contract & fix failing test: approval-gating now opt-in (`require_approval` arg / `REQUIRE_APPROVAL` env), default off (`reply`→`reply`); both modes tested. *(app/llm/agent.py)*
- [x] Wire episode persistence: `EpisodeRepository.save_episode()` from `/baseline`; `/replay` falls back to DB. *(app/main.py)*
- [x] Wire learning auto-save: above-threshold trajectories persisted from the baseline flow. *(app/main.py, research/sim/learning/trajectory_store.py)*
- [x] Wire telemetry: HTTP middleware records request count/latency/errors + episode start/end. *(app/main.py, telemetry/metrics.py)*
- [x] Fix LLM cache: removed per-call `_clear_cache()`; keyed by observation hash + TTL/size cap; bypassed under approval. *(app/llm/agent.py)*
- [x] Replace `datetime.utcnow()` with timezone-aware UTC; remove bare `except:`. *(db.py, repositories.py, trajectory_store.py, alerts.py, llm_agent.py)*
- [x] Fixed latent bugs found en route: `Episode.to_dict()` ignored `decisions_json`; `expire_on_commit=True` caused `DetachedInstanceError` on returned ORM objects.
- [x] Updated README/TECHNICAL_REFERENCE "Operational Notes & Constraints" to reflect closed gaps.

> **Carried into Phase 1:** a large set of real source files (`research/benchmark/`, `telemetry/`, `reports/`, `dashboard/`, `docs/`, `research/sim/agents/`, `app/live_api.py`, `app/core/approval.py`, several `tests/`) are present in the working tree but **never committed to git** — the history is missing chunks of the codebase. Phase 1 must commit real source and untrack DB artifacts.

## Phase 1 — Repo hygiene & developer experience ✅ COMPLETE
- [x] Committed the large body of **untracked source** (research/benchmark/, telemetry/, reports/, dashboard/, docs/, research/sim/agents, env/dashboard_api, env/approval, research/sim/learning extras, 7 tests, CI, lockfile) — the history was missing chunks of the codebase.
- [x] Untrack generated CSVs; gitignore `*.db/sqlite`, `artifacts/`, `leaderboard*.csv`, `node_modules/`, `dashboard/dist/`. (Schema is created on startup via `migrate_db()`.)
- [x] Add LICENSE (MIT) + license field, CONTRIBUTING.md, SECURITY.md, CODE_OF_CONDUCT.md, `.env.example`.
- [x] Centralize config in `app/core/config.py` (pydantic-settings, fresh-read); replace scattered `os.getenv` in llm_agent/llm_policy; de-dup `normalize_openai_base_url`.
- [x] Structured logging (`app/core/logging_config.py`) + request-id middleware (X-Request-ID). (Existing prints are intentional CLI output.)
- [x] pre-commit: ruff lint+format + prettier (dashboard) + whitespace/EOF/yaml/toml. `ruff check` clean; repo formatted.
- [ ] eslint for the dashboard is deferred to Phase 4 (frontend tooling) — the dashboard has no eslint config yet.

> Test count: 165 passing (Phase 0 + new config/logging tests). Branch: `phase0-correctness`.

## Phase 2 — Security & API hardening ✅ COMPLETE (decisions: opt-in auth, HITL off by default)
- [x] Opt-in token auth (`API_AUTH_TOKEN`): gates mutating methods via Bearer/X-API-Key; open by default so local/tests/tooling are unaffected.
- [x] CORS middleware (configurable `CORS_ORIGINS`, default `*`).
- [x] Opt-in per-IP rate limiting (`RATE_LIMIT_PER_MINUTE`, default off -> 429).
- [x] Input hardening: `episode_id` validation on replay/episodes/reports; bounded pagination (page>=1, limit<=100).
- [x] Global exception handler -> generic JSON 500 with request_id, no leaked traces.
- [x] `tests/test_security.py` (8 tests). 173 passing.
- [ ] bandit + pip-audit wired into CI -> deferred to Phase 3 (CI pipeline).
- [ ] request-size limits -> deferred (handled at the ASGI server / reverse-proxy layer; revisit if needed).

## Phase 3 — Deployment & CI/CD ✅ COMPLETE (as built then; see note)
> **Superseded in part.** The React dashboard this phase built and shipped was
> **removed** when the product moved to the server-rendered UI in `app/web`
> (see CHANGELOG). Today's reality: the Dockerfile is **single-stage** (no Node
> toolchain), everything runs on port **8000**, and CI has no frontend job.
> The durable outcomes of this phase are the ones below.
- [x] `docker-compose.yml` for one-command local bring-up; non-root container user; healthcheck; `.dockerignore`.
- [x] CI: parallel jobs — lint (ruff), test matrix (3.10/3.11/3.12) + coverage gate (Codecov), typecheck (mypy, informational), security (bandit blocking + pip-audit informational), docker build + smoke (`/health`, `/docs`, `/`, `/login`), inference smoke.
- [ ] Release pipeline (changelog, semver tags, GHCR publish, SBOM) — **deferred**; needs repo settings/secrets and a tagging convention.
- [ ] Pinned base-image digests and a Windows CI leg — deferred (tag-pinned for now).

## Phase 4 — Frontend & UX polish ✅ COMPLETE (superseded)
> **Superseded.** This phase polished the React dashboard (vitest, eslint,
> `dashboard/src/api.ts`, `useDashboardSocket`) — all of which was later
> deleted along with the dashboard itself. The product UI is now
> server-rendered Jinja under `app/web` with no bundler and no Node tooling;
> its coverage lives in `tests/test_web_pages.py`. The `/ws/dashboard`
> WebSocket API outlived its frontend and remains tested (`tests/test_dashboard.py`).
- [ ] Dedicated accessibility & responsive pass on the server-rendered UI — still open.

## Phase 5 — Benchmark & simulation rigor ✅ CORE COMPLETE (additive items deferred)
- [x] Documented `strict_unit_interval` + `atan` reward transform in grader.py; property tests (open-unit + monotonicity sweeps) for both; hard-task weight check. (Used dependency-free sweeps instead of hypothesis.)
- [x] Golden-score snapshot + determinism tests across tasks (CI drift guard via `tests/test_grading_rigor.py`).
- [x] `/baseline` accepts `hybrid` (added to PolicyMode; endpoint test).
- [ ] More YAML scenarios + a pydantic scenario schema validator — deferred.
- [ ] Reproducible benchmark report (JSON+HTML+CSV) as a CI artifact + persist leaderboard to DB — deferred.
- [ ] Improve hybrid's deterministic (no-key) fallback quality (currently scores ~0 without a provider) — deferred.

## Phase 6 — Observability & operations ✅ COMPLETE
- [x] Liveness (`/health/live`) + readiness (`/health/ready`, DB probe -> 200/503) split; lifespan logs startup/shutdown (graceful SIGTERM).
- [x] Prometheus scrape config + Grafana datasource provisioning + optional observability docker-compose; existing Grafana dashboard JSON.
- [x] Alert rule evaluation wired via `/alerts` (verified by test); webhooks restricted to http(s).
- [x] `docs/RUNBOOK.md` (probes, metrics, alerts, log correlation, incident table, security toggles).
- [ ] DB connection pooling tuning + a dedicated LLM cost/latency Grafana panel — deferred (SQLite default; metrics exist).

## Phase 7 — Documentation & API versioning ✅ CORE COMPLETE (/v1 move deferred)
- [x] README gains a Documentation index + Security & Configuration section + new endpoints (it was a single doc, not duplicated).
- [x] `docs/ARCHITECTURE.md` (layers, request flow, components, invariants, design decisions) — covers the architecture-diagram + decision-record intent.
- [x] Enriched OpenAPI metadata + `/version` endpoint; documented versioning policy.
- [x] CI-verified quickstart (inference-smoke + docker smoke jobs).
- [ ] Breaking `/v1` path move + per-endpoint OpenAPI examples — deferred (a `/v1`-only move conflicts with the unversioned `/reset|/step|/state` paths; revisit with a back-compat alias).

---

## Phase 8 — LLM Integration Overhaul ✅ CORE COMPLETE (async conversion deferred)

Bar: Async, multi-provider, function-calling LLM integration. The system works with OpenAI, Azure, Anthropic, Gemini, and local models from the same codebase.

### 8.1 Provider abstraction layer ✅ COMPLETE
- [x] Create `app/llm/providers/` package with `LLMProvider` ABC: `generate()`, `generate_stream()`, capabilities.
- [x] Implement `OpenAIProvider` (OpenAI + Azure), `AnthropicProvider`, `GeminiProvider`, `OllamaProvider`.
- [x] Auto-detect provider from config (endpoint URL, env vars). Registry + factory pattern.
- [x] Add `LLM_PROVIDER`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY` to `.env.example` and `app/core/config.py`.
- [x] Provider-level pricing table (USD per 1M tokens per model) for accurate cost tracking.

### 8.2 Async + streaming ⏳ PARTIAL (sync path done, async deferred)
- [x] Refactored `LLMAgent` to use `OpenAIProvider` internally instead of raw `OpenAI` client.
- [ ] Convert `LLMAgent.get_action()` → `async def`. All internal LLM calls use `await provider.agenerate()`.
- [ ] Convert `Planner.plan()` → async. `HybridPolicy.next_action()` → async.
- [x] `POST /step/stream` SSE endpoint for real-time agent execution trace. *(app/main.py)*
- [ ] `asyncio.Lock` for cache access instead of thread lock.
- [ ] `inference.py` top-level `asyncio.run(main())`.

### 8.3 Function/tool calling ✅ COMPLETE
- [x] Define typed `ToolDefinition` list for all 5 action types (classify, reply, escalate, defer, prioritize).
- [x] Integrated tool calling into `LLMAgent._call_llm_with_fallback()` — uses tools when provider supports them, JSON fallback otherwise.
- [x] Simplify `SYSTEM_PROMPT` — guidelines only, action schema is in tool definitions.
- [x] `_parse_llm_response()` and `_validate_action()` kept as JSON fallback path for non-tool providers.

### 8.4 Structured output gate ✅ COMPLETE
- [x] Tool calling effectively provides structured output for supported providers.
- [x] Graceful fallback: tool calling → JSON parsing → prompt-based.
- [x] Provider capability flags: `"structured_output"`, `"tools"`, `"streaming"`.

### 8.5 Circuit breaker ✅ COMPLETE
- [x] Per-(provider, model) circuit breaker: 3 failures → open 30s → half-open → retry.
- [x] Multi-provider failover: if primary is open, try secondary provider.
- [x] `AllProvidersFailedError` — last-resort fallback to deterministic research.baseline.
- [ ] Prometheus metrics: `circuit_breaker_state`, `circuit_breaker_trips_total` — deferred (OTEL migration in Phase 9).

> **Tests:** 452 passing (all existing + updated mocks for provider-based architecture). New test file `tests/test_providers.py` (3 tests) exists; additional provider/circuit-breaker tests deferred.

---

## Phase 9 — LLMOps Infrastructure

Bar: LLM observability, evaluation, and prompt management at production quality.

### 9.1 OpenTelemetry distributed tracing
- [ ] Replace custom `PrometheusMetrics` with OTEL API: `TracerProvider`, `MeterProvider`.
- [ ] Instrument: gateway middleware, env step, grader, LLM call, DB query — each with a named span + attributes.
- [ ] OTLP exporter (console + HTTP). Add Tempo datasource to Grafana dashboard.
- [ ] Dual-write: OTEL + legacy Prometheus during transition. Phase out legacy after verification.

### 9.2 A/B evaluation pipeline
- [ ] `scripts/run_ab_test.py` — compare two agent configs across N seeds, paired t-test, 95% CI, Cohen's d.
- [ ] HTML report: summary table, delta heatmap, per-task breakdown, step-by-step trajectory diff.
- [ ] Integration with `BenchmarkRunner` + `research/benchmark/significance.py`.

### 9.3 Prompt registry
- [ ] Move prompts to `prompts/` directory as versioned `.jinja2` templates.
- [ ] `scripts/prompt_cli.py` — diff versions, validate rendering, switch active version.
- [ ] `PROMPT_VERSION` config key — select active version at runtime.
- [ ] Backward compat: default version loads current prompts.

### 9.4 Evaluation dashboard (React)
- [ ] API endpoints: `POST /eval/run`, `GET /eval/job/{id}`, `GET /eval/jobs`.
- [ ] React components: `EvalConfig` (form), `EvalProgress` (progress bar), `EvalResults` (charts + table), `EvalHistory` (list).
- [ ] Chart types: grouped bar (task × agent), box plot (score distribution), radar (agent strengths).
- [ ] New "Evaluations" tab in the dashboard.

### 9.5 Confidence calibration
- [ ] Compute ECE + calibration curve from benchmark runs.
- [ ] `scripts/calibrate.py` — run calibration benchmark, output report + plot.
- [ ] Grafana panel: calibration curve over time.

> **Tests:** ~50 new tests across 6 test files (tracing, A/B stats, prompt registry, eval runner, calibration, eval dashboard API). CI load-test job verifies SLA.

---

## Phase 10 — Cloud-Native & Production Engineering

Bar: Deployable on Kubernetes with distributed caching, async DB, and performance SLAs.

### 10.1 Helm chart
- [ ] `helm/exec-email-copilot/` — Deployment, Service, Ingress, ConfigMap, Secret, HPA, PDB, ServiceMonitor.
- [ ] `values.yaml` with sensible defaults. `values.prod.yaml` / `values.staging.yaml` overrides.
- [ ] Helm template validation in CI.
- [ ] `helm/README.md` — install, upgrade, rollback instructions.

### 10.2 Async SQLAlchemy
- [ ] Add async engine (`aiosqlite` dev, `asyncpg` prod). `AsyncSession`, `async_sessionmaker`, `get_async_session()`.
- [ ] Convert all repository methods to async. Update route handlers to `async def`.
- [ ] Opt-in via `USE_ASYNC_DB=true`, default sync. Phase out sync after verification.
- [ ] Keep sync path as fallback for minimal-dependency deployments.

### 10.3 Redis distributed cache
- [ ] `app/llm/cache/` — async Redis client with `get_or_compute()`, TTL, namespace isolation.
- [ ] Replace in-memory `_response_cache` dict with Redis-backed cache L2 (memory as L1).
- [ ] No-op when `REDIS_URL` unset — full backward compatibility.
- [ ] Cache hit/miss Prometheus counters.

### 10.4 Load testing suite
- [ ] Enhanced `scripts/loadtest/locustfile.py` — realistic user mix: reset, baseline, leaderboard, health.
- [ ] CI load-test job: 10 users, 60s, assert p95 < 2000ms for baseline, error rate < 1%.
- [ ] HTML report artifact.

### 10.5 Semantic cache
- [ ] Embedding-based cache: flatten observation → embed (sentence-transformers or OpenAI) → cosine similarity search in Redis vector index.
- [ ] Configurable similarity threshold (default 0.92). Opt-in via `SEMANTIC_CACHE_ENABLED`.
- [ ] Fallback chain: exact cache → semantic cache → LLM provider.

> **Tests:** ~30 new tests across 5 test files (Helm validation, async repositories, cache, loadtest locustfile, semantic cache). Integration test with Redis container.

---

## Phase 11 — True Multi-Agent System

Bar: Each specialist is LLM-powered. Agents negotiate. System is extensible via plugins.

### 11.1 LLM-powered specialist agents
- [ ] `LLMClassifierAgent`, `LLMResponderAgent`, `LLMEscalatorAgent` — each with own system prompt + tool definitions.
- [ ] `CoordinatorAgent` accepts optional `provider` param; LLM specialists when provider given, rule-based fallback when None.
- [ ] Specialist agents report confidence, reasoning, alternatives in decision trace.

### 11.2 Agent-to-agent negotiation
- [ ] `NegotiationRound` protocol: propose → critique → decide. Max 2 rounds, then tiebreaker.
- [ ] Each specialist proposes an action. Others can challenge. Coordinator makes final call.
- [ ] Full negotiation trace logged to `message_log` and exposed via API.

### 11.3 Agent health monitoring
- [ ] OTEL metrics per agent: latency histogram, error counter, confidence histogram.
- [ ] Grafana dashboard panel: agent latency (p50/p95), error rate, confidence distribution.
- [ ] `BaseAgent.execute()` wraps all implementations with automatic metric recording.

### 11.4 Plugin system
- [ ] `research/sim/agents/plugin.py` — discover agents via `exec_email_copilot.agents` entry points.
- [ ] `create_agent(name)` — built-in + plugin resolution.
- [ ] `docs/AGENT_PLUGINS.md` — how to write, package, and install a custom agent plugin.

> **Tests:** ~25 new tests across 4 test files (LLM specialists, negotiation, agent metrics, plugin system).

---

## Phase 12 — Portfolio Storytelling

Bar: The project presents itself as a world-class portfolio piece — documentation, visuals, interactivity.

### 12.1 Architecture Decision Records (ADRs)
- [ ] `docs/adr/` directory with template and 5+ records:
  - ADR-001: Use strict unit interval for scores
  - ADR-002: Opt-in auth (open by default)
  - ADR-003: Provider abstraction (Phase 8)
  - ADR-004: Async migration (Phase 10)
  - ADR-005: Function calling over JSON (Phase 8)

### 12.2 Jupyter notebook demo
- [ ] `notebooks/demo.ipynb` — end-to-end walkthrough: install → env → baseline → grade → visualize → compare agents.
- [ ] Interactive widgets: dropdown for task/persona/agent selection.
- [ ] Matplotlib charts: score breakdown, agent comparison bar chart, score distribution histogram.

### 12.3 Architecture diagram (visual)
- [ ] PlantUML + Mermaid diagram showing all layers: clients → API → environment → agents → LLM providers → infrastructure.
- [ ] Embedded in `docs/ARCHITECTURE.md` and `README.md`.
- [ ] CI step to render PlantUML on push (optional).

### 12.4 Benchmark results page
- [ ] `docs/research/benchmark/` — interactive HTML page (Chart.js) with grouped bar charts, radar charts, data tables.
- [ ] `scripts/generate_benchmark_page.py` — generate from benchmark JSON output.
- [ ] GitHub Pages deployment workflow.

> **Tests:** 0 new tests (documentation only). Verification through manual review.

---

## Execution notes
- Work phase-by-phase, starting from Phase 8, on a feature branch; keep tests green at every commit.
- Each phase ends with proving tests + a docs update so the claim↔test↔code loop never reopens.
- Pause for product decisions at phase boundaries (default provider priority; plugin security model).

---

## Known defects in the LLM layer

Found while building the product drafter (`app/llm/drafter.py`) and recorded here
rather than fixed, because the drafter routes around all of them by using the
OpenAI/Azure provider — the only complete implementation. Anything that moves the
product onto a second provider has to clear this list first.

- [ ] **Anthropic tool-calling is broken end to end.** `app/llm/agent.py` passes
  OpenAI-shaped `TOOL_DEFINITIONS` (`{"type": "function", "function": {...}}`)
  straight through to `anthropic_provider.py`, which expects
  `{"name", "description", "input_schema"}`. Separately, the provider sets
  `arguments=str(block.input)` — a Python dict repr with single quotes — and
  `app/llm/tools.py` then calls `json.loads` on it, which cannot succeed.
- [ ] **Non-OpenAI providers get the wrong model name.** `_detect_provider` in
  `app/llm/providers/__init__.py` passes `settings.model_name` (default
  `gpt-4o-mini`) to the Anthropic, Gemini and Ollama constructors, overriding
  their own defaults. An Anthropic key alone yields a 404 unless `MODEL_NAME` is
  also set.
- [ ] **`CircuitBreakingProvider` defines no `agenerate`.** Async callers fall
  through to the base class, which calls the *synchronous* `generate()` — so an
  async request blocks the event loop. The `AsyncOpenAI` client is unreachable
  through auto-detection.
- [ ] **`app/llm/cache/redis_cache.py` cannot run.** It does `async with
  self._lock` where `_lock` is a `threading.Lock`, which has no `__aenter__`.
  Raises `TypeError` the first time `REDIS_URL` is set. `semantic_cache.py` is
  likewise unreferenced.
- [ ] **Two approval systems.** The simulator's `ApprovalRequestStore`
  (`app/core/approval.py`) is process-global and not tenant-scoped, and
  `REQUIRE_APPROVAL` drives only that one. The product uses its own DB-backed
  gate (`app/copilot/pipeline.py` → `saas_proposed_actions`). The two should be
  reconciled before `REQUIRE_APPROVAL` is documented as a product setting.
- [ ] **Dead prompt duplication.** `app/llm/prompts/registry.py` holds byte-copies
  of `SYSTEM_PROMPT` (`agent.py`) and `PLANNER_SYSTEM_PROMPT` (`llm/policy.py`);
  only the newer `executive_draft` prompt there is actually used. The planner
  copy also has broken `{{...}}` escaping for its own `.replace`-based renderer.
