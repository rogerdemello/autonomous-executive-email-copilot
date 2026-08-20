# Completion Plan — demo-perfect now, world-class agent next

Audit date: 2026-08-20. Baseline: 748/748 tests green, ruff clean, demo path
(seed → login → inbox → approvals) verified working end-to-end by hand.
Everything below is a confirmed finding with file:line evidence, ordered into
phases by blast radius: what breaks a live demo first, what fails an interview
code-read second, polish and ambition after.

Legend: 🔴 demo/interview-fatal · 🟠 found-in-five-minutes · 🟡 polish · effort S/M/L

---

## Phase 0 — Repo integrity (do first, ~1 hour) 🔴

The single worst defect is not in the code — it's in git.

- [ ] **Commit the in-flight drafter work, including the untracked modules.**
      `app/llm/parsing.py`, `app/llm/drafter.py`, `app/llm/draft_cache.py`,
      `tests/test_llm_drafter.py` are untracked, but the *tracked, modified*
      files import them (`app/llm/agent.py:23`, `app/saas/sync_service.py:96,122,161`)
      and README links to them. One `git commit -a` (which skips untracked files)
      and every fresh clone dies at import. 18 of the 748 tests live in the
      untracked test file. (S)
- [ ] **Push `saas-foundation`.** The branch is 13 commits ahead of `main` and has
      no upstream — all recent work exists only on this machine. (S)
- [ ] **Generate and commit `data/demo/drafts.json`** (`seed_demo.py --fresh --with-llm`,
      needs a key once). Without it the demo's centerpiece — the *model-drafted*
      chip — never appears; today the seeder reports "5 authored fixture prose,
      6 generic sentence". (S)

## Phase 1 — Demo-fatal: nothing a judge can click may 404 (1–2 days) 🔴

- [ ] **Retire every `/dashboard/` reference in live code.** The React dashboard
      was deleted; four production paths still point at it and 404:
      - OAuth success page after connecting Gmail → `app/saas/mailbox_routes.py:101`.
        Replace the bare page with a redirect to `/app/inbox` **plus an automatic
        first sync** (the demo-connect path already does this; the real path doesn't).
      - SSO callback → `app/saas/routes.py:296` redirects to `/dashboard/?sso_token=…`
        and **never sets the session cookie**. Fix: `set_session_cookie` + redirect
        to `/app/inbox`. Fix `tests/test_saas_sso.py:176`, which asserts the bug.
      - Password-reset email link → `routes.py:181`; invite email → `routes.py:348`.
        Point at real pages (see Phase 3 reset flow). (M)
- [ ] **Fix disconnect → reconnect corruption** (most reproducible live failure):
      `MailboxRepository.delete` (`repository.py:357`) removes only the connection
      row, though `connect.html:41` promises cleanup. Orphaned messages keep the
      "11 awaiting" badge alive, approving one returns raw JSON 404, and
      reconnecting **duplicates the inbox** (dedup key includes `connection_id`,
      `models_db.py:226`). Cascade-delete processed messages + proposed actions on
      disconnect, and extend `tests/test_web_pages.py:437` to assert it. (M)
- [ ] **Human error pages for the web app.** Approve double-click, stale action,
      or missing connection currently dump `{"detail": …}` raw JSON
      (`app/web/routes.py:537,634,651,666-672`). Add an exception handler for
      `/app/*` that renders a small error template with a "back" link. (S)
- [ ] **Escalations render with no draft body** — all 6 demo escalations show an
      empty prose panel (`policy.py:64` emits no content; `sync_service.py:99`
      substitutes authored prose only for `reply`). The in-flight drafter writes
      handover notes; make sure the fixture/cache fallback covers escalations too,
      so the approvals page is never blank. (S)
- [ ] **`/dashboard/eval/*` 500s on a default install** (`ModuleNotFoundError:
      aiosqlite` — `app/live_api.py:101,132`; the dep is only in an optional
      extra). Either lazy-guard with a clean 501/404, add the dep, or drop the
      routes. Add the missing test. (S)
- [ ] **Fix the two stale ROADMAP phases and the broken pre-deploy command** —
      `DEPLOYMENT_GUIDE.md:100` (`python inference.py` → `python research/inference.py`),
      ROADMAP Phases 3–4 describe a deleted React dashboard as ✅ COMPLETE. (S)

## Phase 2 — Interview-fatal: security and honesty of claims (2–3 days) 🔴🟠

Security walkthrough questions land exactly here.

- [ ] **Token-type allowlist in session resolution.** `auth.py:181` rejects only
      `typ=="license"`; password-reset tokens (60 min) and OAuth `state` tokens
      (15 min, **sent to Google in a query string**) are accepted as full browser
      sessions. Require `typ=="session"` explicitly. Add tests for each rejected
      type. (S — highest security value per line changed)
- [ ] **Silent fake data on broken connections.** A google/microsoft connection
      with no stored token gets `FakeProvider()` and fills the user's "Gmail"
      with four invented fixture emails (`provider_factory.py:89-94`). Replace
      with a connection error state: wire the dead `MailboxRepository.set_status`,
      surface "reconnect needed" in the UI. Also catch `DecryptionError` from
      `vault.decrypt` (`provider_factory.py:97`) — today a key rotation turns
      every sync into a 500. (M)
- [ ] **Real-OAuth default config is broken.** With `OAUTH_REDIRECT_BASE_URL`
      unset, the authorize URL carries a *relative* redirect_uri
      (`mailbox.py:81` → `oauth.py:117`) and Google rejects it — while the UI
      shows the provider as "Ready". Derive from `request.base_url` on the
      authorize leg (the callback already does), and make both legs agree. Add a
      test without the env var (every existing test sets it). (M)
- [ ] **Audit-log truthfulness.** Web login failures write no audit row
      (`web/routes.py:317-329`); API login failures write `org_id=None` rows the
      Activity page can never show (`routes.py:126`) — while `activity.html:88`
      claims "every security-relevant action". Resolve org by email on failure,
      audit the web path, or soften the claim. (S)
- [ ] **Gate or quarantine the unauthenticated benchmark surface.** `GET` is
      globally open (`security.py:91`), so `/approval/pending` is an
      unauthenticated cross-tenant read, and `POST /dashboard/reset` resets a
      process-global simulator for everyone. Cleanest fix that also resolves the
      "two approval systems in one OpenAPI doc" smell: mount the research/benchmark
      API under `/research/*` (or behind `RESEARCH_API_ENABLED`), keep the product
      API clean, and document the split as an architectural feature. (M)
- [ ] **Gmail timestamps.** `gmail.py:117` stores epoch-millis; the UI renders
      `1723800000000` for every real Gmail message and mixed-provider sorting is
      wrong (string column). Normalize to ISO-8601 UTC at fetch time. (S)
- [ ] **Make licensing real or say it isn't.** Feature flags have zero enforcing
      call sites (`licensing.py:23-28`), expiry is display-only (`billing.py:104`),
      seat limits fire only on an API with no UI, revocation is unreachable.
      Minimum honest bar: enforce `is_valid` + feature gates on SSO, audit-log
      page, and sync; keep the rest documented as sales-led. (M)

## Phase 3 — Product completeness: the missing UIs (2–3 days) 🟠

The landing page promises these; all are currently curl-only.

- [ ] Forgot-password: request page + emailed-token reset page (closes the dead
      `reset_token` flow). (M)
- [ ] Settings → members: invite / change role / remove (API + RBAC already
      exist and are solid — this is templates + routes). Seat-limit errors
      finally become reachable. (M)
- [ ] Settings → account: change password, activate license key. (S)
- [ ] Settings → danger zone: export bundle (download JSON) and delete-org with
      slug confirmation — the backend (`data_lifecycle.py`) is done and owner-gated.
      (S)
- [ ] `REQUIRE_APPROVAL` doc fix: it drives only the simulator store, not the
      product gate (`pipeline.py:19` is unconditional — which is the *better*
      story: "outbound approval is not a setting, it's an invariant"). Say that.
      (S)
- [ ] Sync staleness indicator ("last synced 4h ago · Sync now") — honest until a
      background worker exists. (S)

## Phase 4 — LLM layer: make the multi-provider claim true, or scope it (3–4 days) 🟠

Recommendation: **fix Anthropic properly (it's the demo-credible second
provider), keep Gemini/Ollama behind documented extras, delete the dead weight.**
All confirmed defects, per the audit:

- [ ] Anthropic: translate OpenAI-shaped tools → `input_schema` form at the
      provider boundary; `json.dumps(block.input)` instead of `str()`
      (`anthropic_provider.py:93,162`); use the top-level `system` param
      (`:51-52`). Add `anthropic` to an extras group. (M)
- [ ] Model-name resolution: stop forcing `settings.model_name` (`gpt-4o-mini`)
      onto every provider (`providers/__init__.py:70,79,94,128-142`); per-provider
      defaults, `MODEL_NAME` as override only. Same for the `larger_model` retry
      path (`agent.py:728`). (S)
- [ ] Circuit breaker: implement `agenerate` (today async callers run the sync
      client on the event loop), actually wire `secondary` (failover is documented
      and never constructed, `providers/__init__.py:125`), and handle
      `AllProvidersFailedError` explicitly in the agent. (M)
- [ ] Delete or fix `app/llm/cache/` (redis_cache raises `TypeError` on first
      use — `threading.Lock` under `async with`; semantic_cache is unreferenced
      and blocks the loop). Deleting is defensible: the ROADMAP already treats
      Redis as future work. (S)
- [ ] Deduplicate prompts: `registry.py` carries a byte-copy of the agent prompt
      and a *divergent truncated* copy of the planner prompt with broken `{{}}`
      escaping. Single source of truth; make the agent read the registry. (S)
- [ ] Ollama async: missing `_get_async_client` override sends "local" requests
      to OpenAI (`ollama_provider.py:33`). Fix or drop the async path — note the
      **entire async agent path has zero callers** (`agent.py:752`, `policy.py:314`);
      deleting it is a legitimate simplification. (S/M)
- [ ] Capability-flag truthfulness (Anthropic/Gemini advertise streaming and
      raise `NotImplementedError`; Gemini ignores `response_format` and reports
      $0.0000 cost). Flags must match implementations; unknown pricing should
      say "unknown", not zero. (S)

## Phase 5 — Benchmark reproducibility (1 day) 🟠

The research story is the interview differentiator; it must reproduce exactly.

- [ ] `README.md:166` says 3 seeds; `DEFAULT_SEEDS` is 8 (`runner.py:27`). The
      published table is not reproducible by the published command. Regenerate
      the table at 8 seeds or pin the command with `--seeds 42 43 44`. (S)
- [ ] `--agents` help text advertises 2 default agents, 3 run (`run_benchmark.py:73`);
      `BenchmarkRunner.run_all()` includes the key-requiring LLM agent ungated
      (`runner.py:76-83`); `--record-history` writes to the deleted `benchmark/`
      dir; default `--out` drops artifacts into the `reports/` source package.
      Fix all four defaults. (S)
- [ ] Either document `ab_eval.py` / `calibration_cli.py` (they exist and only
      `--help` is tested) or fold them into the ROADMAP 9.x items they satisfy. (S)

## Phase 6 — Docs truth pass (½ day, after code settles) 🟡

Single sweep with the audit table: ROADMAP stale phases/counts/paths, WHITEPAPER
"FastAPI + React", `.pre-commit-config.yaml` prettier over deleted `dashboard/`,
BENCHMARK.md line refs, 7 test docstrings citing `env/*`, `pipeline.py:19` stale
mirror comment, TECHNICAL_REFERENCE `APP_API_BASE_URL` wording. Then re-verify
every README claim against the tree (the test-count badge is currently accurate —
keep it that way with the entrypoint test pattern).

## Phase 7 — Deploy story (1–2 days) 🟡

- [ ] Helm chart cannot start a pod: `runAsUser: 1000` vs image uid 10001;
      `readOnlyRootFilesystem` + default SQLite; PVC mounted at `/data` while the
      app writes `/app/data`; `LLM_MODEL`/`LLM_PROVIDER` declared but never
      injected (and `LLM_MODEL` isn't even the config key). Fix values +
      deployment, inject `DATA_DIR`, use `/health/ready` for readiness, add
      `helm template` lint to CI. (M)
- [ ] `OTEL_EXPORTER_OTLP_ENDPOINT` is read via `getattr` off Settings that
      doesn't define it (`main.py:132`, `extra="ignore"`) — OTLP can never be
      enabled. Add the field. (S)
- [ ] `.dockerignore`: exclude `tests/`, `docs/`, `build/`, `*.egg-info`,
      `.hypothesis/`. (S)

## Phase 8 — World-class agent trajectory (post-hackathon, ordered)

1. ✅ **Background sync worker** (`app/saas/sync_worker.py`; per-connection
   cadence from persistent `last_synced_at`, stable jitter, per-connection
   failure isolation and backoff) + batched provider writes (Gmail
   `batchModify` for label groups). Opt-in via `SYNC_WORKER_ENABLED`; the Helm
   chart enables it.
2. ✅ **Learning from the approval queue** (`app/saas/learning.py`).
   Edit-before-approve in the UI/API (outcome `edited`, original kept);
   rejected (action_type, sender_role) pairs downgrade to deferral with the
   reason on the action; accepted drafts become few-shot voice examples;
   "what the copilot has learned" panel on /app/approvals + `/inbox/learning`.
3. ✅ **Draft quality evals in CI** (`app/llm/draft_eval.py`,
   `scripts/eval_drafts.py`). Deterministic rubric gates every push; nightly
   workflow adds an LLM judge when a key exists. Baseline 10/11 — the one flag
   is the model's real invented "25 September" deadline, kept as proof.
4. ✅ **True multi-provider + failover**. Phase 4 built the translation and the
   wired secondary; `tests/test_provider_wire_fixtures.py` now pins the exact
   payload each provider sends against recorded fixtures
   (`tests/fixtures/provider_wire/`, regenerate with `REGEN_WIRE_FIXTURES=1`)
   and proves the product path: the drafter, behind the real circuit breaker,
   serves prose from the secondary family when the primary raises mid-call.
5. ✅ **Draft-then-verify agent loop** (`app/llm/verifier.py`): rubric always +
   model fact-check when live drafting is on, verdict stored on the action and
   shown as a "verified" / "check flagged" chip with the exact notes.
6. ✅* OTEL spans now cover the value loop (`inbox.sync`, `llm.draft`,
   `inbox.approve` — no-ops without OpenTelemetry installed), and the
   calibration report is wired: `scripts/export_calibration.py` turns the
   approval queue's (draft confidence, human outcome) pairs into
   `calibration_cli.py` input, closing the product→research loop. Postgres
   opt-in already exists via `DATABASE_URL`; *an async DB layer is consciously
   deferred — the async agent path had zero callers, and adding an async ORM
   for it would be complexity without a customer.

---

## Suggested execution order

Phases 0–1 before any demo (≤2 days, all 🔴). Phases 2–3 before interviews
(the security items in Phase 2 are the ones a code-walkthrough finds). Phases
4–5 make the two headline claims — multi-provider and reproducible benchmark —
literally true. 6–7 are sweep work. Phase 8 is the roadmap slide *and* the next
build.

Keep the discipline that already distinguishes this repo: every fixed claim gets
a test that pins it (the `tests/test_entrypoints.py` pattern), and the ROADMAP's
"Known defects" section stays honest — several of its entries are confirmed and
several new ones join it from this audit.
