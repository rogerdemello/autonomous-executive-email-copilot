# Roadmap: Autonomous Executive Email Copilot → World-Class

**Scope:** Comprehensive (eval-benchmark + production product + flagship OSS), all priority areas, **correctness first**.

**Guiding principle:** *Make every claim in the docs true and tested before adding anything new.* The project already has impressive surface area; world-class means the depth matches the surface.

## Invariants (must not break)
- OpenEnv validator parity: `inference.py` log format (`[START]/[STEP]/[END]`) and the open-interval `(0,1)` score contract.
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
- [x] Structured logging (`env/logging_config.py`) + request-id middleware (X-Request-ID). (Existing prints are intentional CLI/validator output.)
- [x] pre-commit: ruff lint+format + prettier (dashboard) + whitespace/EOF/yaml/toml. `ruff check` clean; repo formatted.
- [ ] eslint for the dashboard is deferred to Phase 4 (frontend tooling) — the dashboard has no eslint config yet.

> Test count: 165 passing (Phase 0 + new config/logging tests). Branch: `phase0-correctness`.

## Phase 2 — Security & API hardening
- [ ] AuthN/Z middleware (configurable; enforced on mutating/sensitive routes).
- [ ] CORS middleware (configurable origins).
- [ ] Rate limiting on `/baseline`, `/benchmark`, LLM paths.
- [ ] Input hardening: `episode_id` validation, bounded pagination, path-traversal guards, request-size limits.
- [ ] Global exception handler; startup secret validation; no leaked traces/secrets.
- [ ] `tests/test_security.py`; bandit + pip-audit clean in CI.

## Phase 3 — Deployment & CI/CD
- [ ] Multi-stage Dockerfile that builds `dashboard/dist`; non-root; pinned digest; layer caching; healthcheck; unify on port 7860; fix DEPLOYMENT_GUIDE port refs.
- [ ] `docker-compose.yml` for local bring-up.
- [ ] CI matrix (Python 3.10–3.12 × ubuntu/windows): ruff, mypy, pytest+coverage gate (Codecov), bandit, pip-audit, frontend (npm ci, tsc, eslint, vitest, build), Docker build + smoke (`/health`,`/docs`), inference smoke.
- [ ] Release: changelog, semver tags, GHCR publish, SBOM.

## Phase 4 — Frontend & UX polish
- [ ] vitest + @testing-library/react; eslint/prettier; component + mocked-API tests.
- [ ] Robust API layer: env-driven base URL, timeouts, retry/backoff, error boundaries, loading/empty states.
- [ ] Wire `/ws/dashboard` WebSocket with auto-reconnect (replace polling).
- [ ] Accessibility & responsive pass.

## Phase 5 — Benchmark & simulation rigor
- [ ] Document/justify `strict_unit_interval` + `atan` reward transform; hypothesis property tests; golden-trajectory metric→score tests.
- [ ] Determinism golden-score snapshots across seeds/personas/tasks; CI drift guard.
- [ ] More YAML scenarios + schema validation; tests for adversarial/conflicting-deadline generators.
- [ ] `/baseline` accepts `hybrid`; finish multi-agent benchmark; reproducible JSON+HTML+CSV report as CI artifact; persist leaderboard to DB.

## Phase 6 — Observability & operations
- [ ] Prometheus scrape + provisioned Grafana; tested alert rules; liveness/readiness split; graceful shutdown; DB pooling; LLM cost/latency dashboard; runbook.

## Phase 7 — Documentation & API versioning
- [ ] Slim duplicated README into crisp top-level + `docs/`; sync TECHNICAL_REFERENCE; architecture diagram + ADRs (grading transform, HITL, multi-agent); version API (`/v1`) with OpenAPI examples; CI-verified quickstart.

---

## Execution notes
- Work phase-by-phase, Phase 0 first, on a feature branch; keep tests green at every commit.
- Each phase ends with proving tests + a docs update so the claim↔test↔code loop never reopens.
- Pause for product decisions at phase boundaries (default auth posture; HITL-on-by-default in API).
