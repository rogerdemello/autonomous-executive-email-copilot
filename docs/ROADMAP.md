# Roadmap: Autonomous Executive Email Copilot → World-Class

**Scope:** Comprehensive (eval-benchmark + production product + flagship OSS), all priority areas, **correctness first**.

**Guiding principle:** *Make every claim in the docs true and tested before adding anything new.* The project already has impressive surface area; world-class means the depth matches the surface.

## Invariants (must not break)
- OpenEnv validator parity: `inference.py` log format (`[START]/[STEP]/[END]`) and the open-interval `(0,1)` score contract.
- Deterministic baseline given `(task, seed, persona)`.
- No breaking changes to existing API response shapes without versioning (`/v1`).

---

## Phase 0 — Make it honest & green (correctness foundation)
Bar: `pytest` 100% green, zero deprecation warnings, every doc claim backed by a test.

- [ ] Resolve HITL/agent contract & fix failing test: make approval-gating opt-in (`REQUIRE_APPROVAL`), default off for raw agent (`reply`→`reply`), on in API/product path; update `test_llm_agent.py`. *(env/llm_agent.py:579, tests/test_llm_agent.py:71)*
- [ ] Wire episode persistence: `EpisodeRepository.save_episode()` from `/baseline`; `/replay` & `/episodes` read DB. *(env/api.py:64,182)*
- [ ] Wire learning auto-save: persist above-threshold trajectories from baseline/grader flow. *(env/learning/trajectory_store.py)*
- [ ] Wire telemetry: increment counters/histograms from API handlers via middleware. *(telemetry/metrics.py, env/api.py)*
- [ ] Fix LLM cache: remove `_clear_cache()` per call; key by observation hash + TTL/size cap. *(env/llm_agent.py:435)*
- [ ] Replace `datetime.utcnow()` with timezone-aware UTC; remove bare `except:` (env/llm_agent.py:565).
- [ ] Update README/TECHNICAL_REFERENCE "Operational Notes & Constraints" as gaps close.

## Phase 1 — Repo hygiene & developer experience
- [ ] Untrack `data/episodes.db`, `env/data/trajectories.db`; gitignore; create schema on startup.
- [ ] Add LICENSE (MIT), CONTRIBUTING.md, SECURITY.md, CODE_OF_CONDUCT.md, `.env.example`.
- [ ] Centralize config in `env/config.py` (pydantic-settings); replace scattered `os.getenv`; de-dup `_normalize_openai_base_url`.
- [ ] Structured logging + request-id middleware; replace prints.
- [ ] pre-commit: ruff (Python), eslint+prettier (TS), whitespace/EOF.

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
