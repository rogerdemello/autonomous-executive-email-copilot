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

- [x] Resolve HITL/agent contract & fix failing test: approval-gating now opt-in (`require_approval` arg / `REQUIRE_APPROVAL` env), default off (`reply`→`reply`); both modes tested. *(env/llm_agent.py)*
- [x] Wire episode persistence: `EpisodeRepository.save_episode()` from `/baseline`; `/replay` falls back to DB. *(env/api.py)*
- [x] Wire learning auto-save: above-threshold trajectories persisted from the baseline flow. *(env/api.py, env/learning/trajectory_store.py)*
- [x] Wire telemetry: HTTP middleware records request count/latency/errors + episode start/end. *(env/api.py, telemetry/metrics.py)*
- [x] Fix LLM cache: removed per-call `_clear_cache()`; keyed by observation hash + TTL/size cap; bypassed under approval. *(env/llm_agent.py)*
- [x] Replace `datetime.utcnow()` with timezone-aware UTC; remove bare `except:`. *(db.py, repositories.py, trajectory_store.py, alerts.py, llm_agent.py)*
- [x] Fixed latent bugs found en route: `Episode.to_dict()` ignored `decisions_json`; `expire_on_commit=True` caused `DetachedInstanceError` on returned ORM objects.
- [x] Updated README/TECHNICAL_REFERENCE "Operational Notes & Constraints" to reflect closed gaps.

> **Carried into Phase 1:** a large set of real source files (`benchmark/`, `telemetry/`, `reports/`, `dashboard/`, `docs/`, `env/agents/`, `env/dashboard_api.py`, `env/approval.py`, several `tests/`) are present in the working tree but **never committed to git** — the history is missing chunks of the codebase. Phase 1 must commit real source and untrack DB artifacts.

## Phase 1 — Repo hygiene & developer experience ✅ COMPLETE
- [x] Committed the large body of **untracked source** (benchmark/, telemetry/, reports/, dashboard/, docs/, env/agents, env/dashboard_api, env/approval, env/learning extras, 7 tests, CI, lockfile) — the history was missing chunks of the codebase.
- [x] Untrack generated CSVs; gitignore `*.db/sqlite`, `artifacts/`, `leaderboard*.csv`, `node_modules/`, `dashboard/dist/`. (Schema is created on startup via `migrate_db()`.)
- [x] Add LICENSE (MIT) + license field, CONTRIBUTING.md, SECURITY.md, CODE_OF_CONDUCT.md, `.env.example`.
- [x] Centralize config in `env/config.py` (pydantic-settings, fresh-read); replace scattered `os.getenv` in llm_agent/llm_policy; de-dup `normalize_openai_base_url`.
- [x] Structured logging (`env/logging_config.py`) + request-id middleware (X-Request-ID). (Existing prints are intentional CLI output.)
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

## Phase 3 — Deployment & CI/CD ✅ COMPLETE (release pipeline deferred)
- [x] Multi-stage Dockerfile that **builds the dashboard** (Node stage → dist copied into Python runtime) so `/dashboard` serves; non-root user; layer-cached deps; healthcheck; unified on port 7860; `.dockerignore`. Fixed DEPLOYMENT_GUIDE port refs.
- [x] Fixed the dashboard build (never compiled): vite-env.d.ts + unused-symbol errors. Fixed `server/app.py` undefined `app` export.
- [x] `docker-compose.yml` for one-command local bring-up.
- [x] CI: parallel jobs — lint (ruff), test matrix (3.10/3.11/3.12) + coverage gate (70%, current 77%, Codecov), typecheck (mypy, informational), security (bandit blocking + pip-audit informational), frontend (npm ci + tsc + build), docker build + smoke (`/health`, `/docs`, `/dashboard/`), inference smoke.
- [ ] Release pipeline (changelog, semver tags, GHCR publish, SBOM) — **deferred**; needs repo settings/secrets and a tagging convention.
- [ ] Pinned base-image digests and a Windows CI leg — deferred (tag-pinned for now).

> Note: Docker image build verified via the dashboard build + CI; not built locally (daemon was down). Run `docker compose up --build` to verify end-to-end.

## Phase 4 — Frontend & UX polish ✅ COMPLETE (a11y pass deferred)
- [x] vitest + @testing-library/react (jsdom); eslint flat config + prettier; api + Inbox tests (6 passing). CI frontend job runs eslint + prettier + vitest + tsc + build.
- [x] Robust API layer (`dashboard/src/api.ts`): timeouts (AbortController), retry/backoff, typed `ApiError`; Inbox + App refactored onto it.
- [x] Live `/ws/dashboard` WebSocket via `useDashboardSocket` (ping/pong + capped-backoff reconnect); App shows Live/Connected/Disconnected.
- [ ] Dedicated accessibility & responsive pass — deferred (semantic/ARIA + mobile layout audit not yet done).

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

## Execution notes
- Work phase-by-phase, Phase 0 first, on a feature branch; keep tests green at every commit.
- Each phase ends with proving tests + a docs update so the claim↔test↔code loop never reopens.
- Pause for product decisions at phase boundaries (default auth posture; HITL-on-by-default in API).
