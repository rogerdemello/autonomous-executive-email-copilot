# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **A product front door.** `/` is now a landing page instead of a redirect into
  the benchmark console, and there is a real sign-in page. New server-rendered UI
  under `app/web`: landing, login, signup, connect a mailbox, triage
  inbox, approvals, activity, and settings — Jinja templates and one stylesheet,
  no bundler and no build step.
- **A demo mailbox** (`provider="demo"`). 14 fixture messages in
  `data/demo/inbox.json`, triaged by the real policy with no API key, no OAuth
  credentials, and no network. `make demo` seeds a ready workspace; see
  [docs/DEMO.md](docs/DEMO.md).
- **Browser sessions.** The same token the API takes as a Bearer header now also
  rides in an HttpOnly `SameSite=Lax` cookie, with a signed CSRF token on every
  mutating form. One identity model across both surfaces.
- **OIDC SSO** with RS256 id_token verification against the issuer's JWKS, and
  `ENVIRONMENT=production`, which refuses to start without `AUTH_SECRET_KEY`
  rather than signing sessions with the well-known development secret.
- **Working schema migrations.** `_run_migration` was a stub and `create_all`
  cannot alter an existing table, so any new column was invisible to deployed
  databases. Additive `ADD COLUMN` steps now run, portably and idempotently.

### Changed

- **Restructured** `env/` into `app/` (the product: `core`, `copilot`, `llm`,
  `saas`, `web`) and `research/` (the benchmark: `sim`, `baseline`, `benchmark`).
  Dependencies point one way — research may import from app, never the reverse.
- **Removed the React dashboard** and its Node CI job; the server-rendered UI
  replaces it, and the Dockerfile is now single-stage. The container and local
  dev both serve on port 8000.
- Collapsed three entrypoints (`main.py`, `server/app.py`, `env.api:app`) into
  `app.main`, which exports both `app` and a `main()` runner.
- Repo-relative paths are anchored once in `app/core/paths.py` instead of six
  separate `Path(__file__).parent.parent` walks.

### Fixed

- **Risk terms were matched as substrings**, so `nda` fired inside *Monday* and
  *agenda*, and `sla` inside *translate*. Any message mentioning a Monday
  deadline was tagged legal risk — escalating it to the legal team and
  compressing its deadline to 60 minutes. Now matched as whole words, with
  explicit `*` stems for cases like `indemnif*`.
- `HEAD /` returned 405, which load balancers and uptime probes read as an outage.
- `.well-known/security.txt` was checked in but never served; the live route
  generated a different body, pointed its `Policy` at a path the app does not
  serve, and hardcoded an `Expires` date that would silently invalidate the file.
- `pip install .[all]` could never resolve — the `all` extra self-referenced a
  package name that does not exist.

- **Render deployment**: a `render.yaml` Blueprint deploys the Docker image as a
  single web service. The container now binds `$PORT` (Render/Cloud Run/Fly.io
  inject it), falling back to 7860 locally.
- One-click **"Let the copilot work"** flow in the dashboard Inbox: runs the
  agent over the whole inbox and shows a plain-language result plus a per-email
  outcome badge.

### Fixed

- **Route shadowing**: `/approval/pending`, `/approval/history`, and
  `/episodes/stats` returned 404 because the parameterized `{request_id}` /
  `{episode_id}` routes were declared first. Reordered the static routes ahead of
  them; added a regression test.

### Removed

- **Hugging Face Spaces deployment**: removed the Space git remote, the README
  Space metadata header, and the HF-Spaces deploy instructions. (The optional
  `HF_TOKEN` LLM provider is unchanged.)

## [1.0.0]

Repositioned from a competition entry into a standalone, world-class personal
project. Behavior of the environment, graders, and API is unchanged.

### Added

- **Real Azure OpenAI `gpt-4o` benchmark results** published in the README and
  `docs/BENCHMARK.md` (3×3×3 grid; LLM 0.17 / 1.00 / 0.62 on easy / medium / hard).
- A `[project.optional-dependencies] dev` group in `pyproject.toml` (pytest,
  pytest-cov, ruff, mypy, bandit, pip-audit); CI installs `.[dev]`.

### Changed

- Removed all competition/hackathon framing: dropped the Hugging Face Space
  README header, the OpenEnv manifest (`openenv.yaml`), the dead `openenv-core`
  dependency, and reworded "OpenEnv validator parity" to the **score/log
  contract** throughout the docs.
- **Consolidated the UI to the React dashboard**; removed the duplicate Streamlit
  console (`streamlit_app.py`) and the `streamlit` dependency.
- **Cleaned up dependency management**: a single runtime `requirements.txt`,
  dev tools in `pyproject` extras, and removed the stale `requirements.lock`.
- Renamed `tests/test_validator_parity.py` → `tests/test_score_contract.py`.

### Fixed

- Azure OpenAI 404s: carry `api-version` via `default_query` (the SDK drops the
  base-URL query when appending the request path).
- LLM agent re-acted on the same email indefinitely (the environment always lists
  every email); it now tracks handled emails and works through the inbox, so it
  actually reaches the model. Benchmark resets the agent per episode.

## [0.1.0] - 2026-06-03

First tagged release: evaluation correctness and reproducibility, an open-source
surface, and production-grade infrastructure.

### Added

- Reproducible **results harness** (`scripts/run_benchmark.py` + `research/benchmark/results_report.py`)
  aggregating scores across `(task, seed, persona)` with 95% confidence intervals,
  emitting `results.json` / `.csv` / `.html`.
- **Benchmark methodology** doc (`docs/BENCHMARK.md`) and a pydantic **scenario schema**
  validator (`research/sim/scenario_schema.py`); optional gated **scenario variants** (`SCENARIO_VARIANTS`).
- **Benchmark Results** table in the README from real deterministic runs.
- **Release pipeline** (`.github/workflows/release.yml`): tag-triggered GHCR image publish,
  SPDX SBOM, and a GitHub Release; issue/PR templates, CODEOWNERS.
- Optional **Postgres** backend (`DATABASE_URL`) with connection pooling (SQLite stays default).
- **LLM cost/latency metrics** (`llm_cost_usd_total`, `llm_tokens_total`, `llm_latency_ms`)
  wired into the agent, with Grafana panels; optional **Locust load test** (`scripts/loadtest/`).
- **Multi-tenant authentication** (`API_TENANTS`, opt-in) and a dashboard **accessibility +
  responsive** pass.

### Changed

- The **multi-agent coordinator** is now task-aware (classifies on classification tasks instead
  of always escalating): easy-task score 0.00 → 0.80, medium 0.00 → 1.00.
- The **hybrid policy** falls back to the strong baseline heuristics with no provider configured
  (no-key scores 0/0/0.03 → 1.00/1.00/0.60). Default benchmark seeds widened 3 → 8.
- Improved **test isolation**: a developer's real `.env` no longer leaks into the config tests.

### Fixed

- **Azure OpenAI authentication**: inject the `api-key` header for Azure hosts (Azure rejects
  `Authorization: Bearer` for resource keys), so Azure-hosted deployments authenticate correctly.

[Unreleased]: https://github.com/rogerdemello/autonomous-executive-email-copilot/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/rogerdemello/autonomous-executive-email-copilot/compare/v0.1.0...v1.0.0
[0.1.0]: https://github.com/rogerdemello/autonomous-executive-email-copilot/releases/tag/v0.1.0
