# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.1.0] - 2026-09-15

The launch pass — turning the repo into something a stranger could be shown —
and the pre-launch hardening that followed it, which turned it into something
that can be left running unattended.

### Added

- **A ceiling on model spend, and a ledger under it.** Cost was computed per
  call and handed to an in-process Prometheus counter: not per-org, and reset
  by every deploy. With drafting on and a worker sweeping every mailbox every
  15 minutes, there was no answer to "what did last week cost, and who ran it
  up?" and nothing stopping one large mailbox from running up a bill.
  `saas_llm_usage` bills every call to the org that caused it, and
  `LLM_MONTHLY_BUDGET_USD` (default 25 per org per calendar month; `0` for no
  ceiling) caps it. At the cap, drafts fall back to rule-based prose — triage,
  verification, commitment tracking and sending are unaffected. Surfaced in
  Settings.
- **A broken mailbox is impossible to miss.** A revoked token flipped the
  connection to `error` and showed only as a chip on `/app/connect`, a page
  nobody opens twice. Everywhere else the inbox quietly stopped filling, which
  is indistinguishable from a quiet week. Now: a banner on every signed-in
  page, one email to the org's admins on the transition, and an audit row.
- **A background-worker heartbeat.** Each sweep records its timestamp and
  summary, the last ten are kept, and `/health/ready` reports whether the
  worker has gone stale. Deliberately not a 503 — the web tier is still
  serving, and cycling a single-instance deployment to fix a background job
  takes the whole product down.
- **`/operator`**, one page that answers "is this working right now?".
  Workspaces with owners and access status, mailboxes by status, the worker's
  heartbeat and recent passes, failed sends awaiting a human, model spend per
  workspace, and recent leads. Behind the existing `OPERATOR_TOKEN`, exchanged
  for a short-lived signed cookie because a browser cannot type an
  `Authorization` header by visiting a URL; the token is never accepted from a
  query string, where it would land in access and proxy logs.
- **`LAUNCH_CHECKLIST.md`** — the remaining deployment steps that need a
  person, in the order they unblock each other.
- **`/privacy` and `/terms`.** Neither existed. Google will not *begin* OAuth
  verification for the restricted `gmail.*` scopes without a published privacy
  policy on the app's own domain, so their absence gated a 6-12 week queue.
  The policy carries the Google Limited Use disclosure, names the LLM
  sub-processor, and justifies each requested scope; tests hold it to
  `app/saas/oauth.py` in both directions.
- **"Waiting on" (`/app/waiting`).** Commitment tracking in both directions —
  what someone owes you, and what you promised in a reply you approved.
  Deterministic extraction (`app/copilot/commitments.py`), dates resolved from
  the words used and stored beside them, spam excluded via the copilot's own
  classification. The capability every competitor is missing.
- **Verification evidence.** `verify_draft` now returns findings, not strings:
  per flagged claim, the sentence in the draft and the line of the source it
  failed against, rendered in Approvals with a "Remove this sentence" button.
  Draft counts and claims caught appear in the app shell on every page.
- **A usable inbox.** Full message bodies (schema v6 — only a 500-character
  preview was stored, so you could not read an email), thread grouping
  (`thread_id` was stored and read by nothing), search, classification and
  priority filters, paging, and keyboard navigation (`j`/`k`/`/`/`e`/`a`).
- **Failed sends are retried** by the background worker, bounded, with the last
  error kept — and shown on Approvals, where they were previously on no page.
- **`scripts/build_landing_metrics.py`** emits the benchmark artifact the
  landing page renders, and `--check` re-verifies it in CI.
  **`scripts/capture_screenshots.py`** and **`scripts/optimize_images.py`**
  make the product screenshots a command rather than an afternoon.
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

- `MailboxRepository.set_status` returns whether the status *changed* rather
  than whether the row was found. The worker re-derives a broken connection's
  state on every sweep; without a transition signal the new notification would
  have fired 96 times a day for as long as the mailbox stayed broken.
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

- **The signed-in app was unusable on a phone.** Every page rendered 620px wide
  inside a 320px viewport, in both themes. One cause in three guises: an `fr`
  grid track, an `overflow-x: auto` nav, and `minmax(300px, 1fr)` all floor at
  min-content unless told otherwise, so the sidebar's widest row set the width
  of the entire application. Thirteen pages now reflow cleanly at 320px.
- **Accessibility gaps on every page Phase 3 did not touch.** The landing
  page's benchmark table and both of `/privacy`'s OAuth scope tables had no
  caption and unscoped headers; the public pages had no skip link at all. Form
  errors now take focus and are associated with their fields — these are full
  page reloads, so the banner exists at load and `role="alert"` alone never
  fires. `tests/test_web_a11y.py` sweeps every page rather than sampling two.
- **A clean `git clone` shipped a broken landing page.** `static/fonts/` and
  `static/img/` were untracked while 18 tracked files referenced them, and the
  package-data glob was non-recursive so a wheel dropped both.
- **The shipped production config disabled the product.** `render.yaml` left
  background sync and LLM drafting off and signup disabled, so the approval
  queue only filled on click, every urgent reply was one canned sentence, and
  self-serve signup was unreachable. SMTP and both OAuth pairs are now marked
  required rather than optional.
- **No tagged release could ever ship.** The release workflow bandit'd and
  covered four directories deleted in the `env/` → `app/` rename.
- **The test suite wrote to the developer's real 80 MB database.** `DATA_DIR`
  is redirected to a temp tree at conftest import.
- **An unreachable database killed the process before FastAPI existed**, so
  `/health/ready` could never report the degraded state it was written for.
  `migrate_db()` moved into the lifespan.
- **Every `/alerts` rule was structurally unfirable.** The metrics parser swept
  an unlabelled line's value into its key, and labelled series were never
  accumulated under their bare name.
- **Spam chips vanished on any tenant past 500 actions** — the label map was
  built from a fixed page of actions and filtered in Python.
- **The audit log recorded no IP for the web surface**, the one column an
  incident review reaches for first, while the JSON API recorded it.
- **Every settings notice rendered twice**, including the one-time invite
  password.
- **Rejected and failed actions rendered as unstyled transparent text**:
  `chip--{{ status }}` had no rule for three of its five values.
- **The Helm chart lints but could not be safely installed.** Its defaults
  signed sessions with a constant published in this repository, and ran two
  replicas of an un-lockable background worker against per-pod SQLite files.
  It now refuses to render those configurations, and CI asserts the refusals.
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
- **Route shadowing**: `/approval/pending`, `/approval/history`, and
  `/episodes/stats` returned 404 because the parameterized `{request_id}` /
  `{episode_id}` routes were declared first. Reordered the static routes ahead of
  them; added a regression test.

### Deployment

- **Render**: a `render.yaml` Blueprint deploys the Docker image as a single web
  service. The container binds `$PORT` (Render/Cloud Run/Fly.io inject it),
  falling back to 8000 locally.

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
