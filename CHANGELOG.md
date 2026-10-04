# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Two themes, and both are the same class of bug — something that works for the
person who built it and fails for the first stranger who tries it.

**What breaks the first time a real mailbox is attached.** The demo provider is
in memory, its bodies are plain text, its drafts are cached, and its mailbox has
no history — so the test suite could be green on all of it.

**What breaks the first time somebody is sent the link.** The live demo, found by
using the deployed site as a stranger would: "Try the live demo" led to a blank
sign-in form, the footer of every page published `sales@example.com`, and a
returning visitor got today's HTML with last month's stylesheet. The entries
below marked *public demo* are that pass.

### Added

- **A one-click demo that cannot dead-end** *(public demo)*. "Try the live demo"
  led to `/login`, which pre-filled a shared account's password — but only if that
  account existed, and on a host whose database is ever empty it never did. The
  visitor got a blank form and no way to obtain credentials. It now builds each
  visitor a **private sandbox workspace** on the click (`app/saas/sandbox.py`): a
  real organization, owner and trial license, with the demo mailbox attached and
  triaged by the same pipeline a real mailbox goes through, in about a second.
  There is no credential to publish (the owner's password is generated and
  discarded; its address is on `.invalid`), nothing to seed, and nothing shared:
  the first visitor to approve the queue no longer empties it for the next.

  It is safe to hang off an unauthenticated button because of what bounds it. A
  per-address rate limit and a hard cap on live sandboxes that *refuses* rather
  than evicting someone mid-demo; a purge at session expiry, on sign-out, on every
  creation and on every worker pass, using the same hard delete a customer's
  erasure uses, and a failed build deletes its own half-made organization; no
  live model call, ever, even where drafting is on; and the same fence as the
  shared account around everything administrative, whether or not the demo is
  currently advertised. The worker never sweeps a sandbox: its mailbox is a
  fixture, and a sweep is a full re-triage to learn nothing. Gated on
  `DEMO_LOGIN_ENABLED` exactly as the shared login was, so production still opts
  in explicitly. `GET /demo` explains it and builds nothing, because crawlers and
  link unfurlers GET.
- **A two-minute tour inside the sandbox** *(public demo)*, and a banner on every
  page saying what it is. Its third step links straight to the draft the verifier
  flagged — "the source never states 25", beside the line it failed against — which
  is the product's whole argument. The counts are read from the workspace, not
  typed: the first draft of that step said "one draft is flagged" while the demo
  holds two.
- **The operator console leaves the demo out** *(public demo)*. Every click adds
  an organization, `/operator` lists workspaces newest-first, and so a week of
  visitors would have put two hundred identical "Northwind Industries" rows above
  the customers the page exists to show — and turned "12 mailboxes syncing" into
  262. Sandboxes are excluded from the workspace list, the member and mailbox
  counts and `GET /operator/orgs`, and counted separately ("plus N live demo
  sandboxes") so the operator can still see the demo is in use.
- **Link previews and crawler files** *(public demo)*. No page carried Open Graph
  or Twitter tags, a canonical URL, a `robots.txt` or a `sitemap.xml`, so a link
  pasted into LinkedIn, Slack or WhatsApp unfurled as a bare title with no
  picture. Now it carries a real 1200×630 screenshot of the inbox (captured by
  `scripts/capture_screenshots.py`, not composed), and the canonical URL is the
  origin actually serving the request — not `Settings.resolved_app_public_url`,
  whose last resort is `http://localhost:8000`. Signed-in pages are `noindex`.
- **Fingerprinted static assets** *(public demo)*. Starlette serves `/static` with
  an ETag but no `Cache-Control`, so freshness was the browser's heuristic: ten
  percent of the file's age. A stylesheet last touched a month earlier stays
  "fresh" for about three days in the cache of everyone who has been before — the
  owner checking their own deploy, a recruiter who looked once — while the HTML
  that references it is new. Found when it happened during this pass. Pages now
  ask for `/static/app.css?v=<digest>`, cached forever; anything unversioned is
  told to revalidate.
- **A keep-alive workflow** (`.github/workflows/keepalive.yml`) for a free-tier
  host that sleeps after fifteen idle minutes and greets the first visitor with
  "service waking up". A stop-gap, documented as one; the fix is a paid plan.
- **A map from each claim to its code and its test** (README, "What to look at"),
  and a CI badge in place of two hand-typed ones — the test-count badge said 1,097
  while the suite had 1,224.

- **Escalation contacts, and an escalation that goes to the right person.** An
  `escalate` action names a *role* — `legal_team`, `chief_of_staff` — because
  that is the judgement the policy can make about a piece of mail. Both
  providers implemented the draft it produced as a **reply**, so the artifact
  that appeared in the mailbox was addressed to the outside party who wrote in,
  carrying a body that said "Escalating to legal_team". The whole affordance of
  a draft is that you press Send, and the approvals page showed only the chip
  "→ legal team", so nothing on screen contradicted it.

  Each workspace now maps every role to a real mailbox in Settings, and the
  provider seam is `create_escalation_draft(message, body, *, to)` — the
  recipient is in the signature, so the wrong one is not reachable by accident.
  It drafts a **forward**, not a reply: Graph uses `createForward`, Gmail builds
  one (no forward endpoint exists) as a new draft with the original quoted and
  deliberately no `threadId`, keeping an internal hand-off out of the customer's
  own conversation. An unconfigured role fails the action with a reason rather
  than guessing — the approvals page says so before the click, and
  `retry_failed_sends` finishes the job once an address is filled in, so the
  approval a human already gave is not thrown away.
- **A readable body for HTML mail** (`app/copilot/providers/html_text.py`,
  stdlib only — untrusted HTML from strangers is not a dependency worth taking
  on). Gmail's extractor took the first `text/plain` part and, finding none,
  fell through to the raw top-level body; a large share of real mail is
  HTML-only. Graph was worse — `body.contentType` is *html* by default and the
  provider took `body.content` whatever the type, so essentially every Outlook
  message was markup. That body is not a display detail: it is what the reader
  shows, what the drafter is handed as "the message", and what the verifier
  checks a draft's claims against.
- **An admin-consent screen for Microsoft.** `Mail.ReadWrite`/`Mail.Send` need
  tenant-admin approval in most managed directories, so hitting that wall is the
  *expected* first outcome of clicking Connect for a corporate customer — and
  Microsoft reports it as `error=access_denied`, indistinguishable from the
  person clicking Cancel. The callback now recognises the consent codes
  (`AADSTS65001`, `AADSTS900941`, `consent_required`) and renders the exact
  adminconsent URL to hand to their IT, in full, in a readonly field. It targets
  `organizations` rather than the configured `common`: personal Microsoft
  accounts have no administrator and cannot grant this. An ordinary refusal
  still renders an ordinary error.
- **A third inbox empty state: "reading your mailbox."** A connected-but-unsynced
  mailbox rendered "nothing matches that filter" — telling someone their inbox
  is empty while the copilot is still reading it. Self-refreshing, via
  `meta refresh`, because the app works without scripting.
- **An integration layer that runs the product against a real provider API**
  (`tests/integration/`). Every bug in this release was in production-only code,
  and every one of them was green — because the suite either exercised a
  provider alone against canned per-call responses, or exercised the product
  against `DemoProvider`, which is a dict in memory whose bodies are plain text
  and whose mailbox is never written to. So there is now a stateful fake of the
  Gmail API, Microsoft Graph, and both token endpoints, installed by patching
  `httpx` itself — below `_httpx_transport`, so the provider's own header
  building and its handling of a body-less 202 are under test too. It is a
  mailbox, not a stub: writes change what later reads return, a Gmail label has
  to exist before it can be applied, a `categories` PATCH replaces the
  collection, a send that omits `In-Reply-To` is recorded as not threaded for
  the recipient, an `$orderby`-less list answers oldest-first, an expired access
  token is a 401 until the refresh token is spent, and a URL no wire serves
  fails the test instead of returning an empty success. On top of it, 55 tests
  that connect a mailbox through the OAuth callback, let the deferred first sync
  run, and then assert on what the product wrote and on what arrived at the
  other end of the wire — including one workspace holding a Gmail account and a
  Microsoft 365 account, which must route identical mail identically and must
  not share fate. Each of the seven provider fixes in this release was
  re-introduced by hand to confirm the layer catches it; all seven fail.

### Fixed

- **"Erase my workspace" left data behind, and on Postgres failed outright.**
  `DataLifecycleService` walked a hand-kept list of tables whose docstring said
  "add it here when you add it". Two tables were added without it:
  `saas_llm_usage` (the model-spend ledger) and `saas_escalation_contacts`, which
  holds the email addresses of the customer's colleagues and has a foreign key to
  the organization. On SQLite — which does not enforce foreign keys — the delete
  succeeded and the addresses stayed; on Postgres it raised an `IntegrityError`.
  Export and delete now walk one list, and a test reads the live SQLAlchemy
  metadata and fails the build if any table with an `org_id` is missing from it,
  after first proving that it notices one that is. `remaining_rows()` answers
  "is anything left?" independently of the list that decides what to delete.
- **`sales@example.com` was published as a live `mailto:` link** in the footer of
  every public page, and as the address to send privacy complaints and
  vulnerability reports to. It was the config default and `SALES_CONTACT_EMAIL`
  was never set on the deployment. Both contact addresses now default to unset,
  an address on a reserved documentation domain (`example.com`, `.example`,
  `.invalid`, `.test`) is treated as unset, and pages fall back to the contact
  form and to GitHub's private vulnerability reporting. `security.txt` also
  stopped *deriving* `security@` from the sales address — a guess at a mailbox
  that may not exist — and no longer publishes `http://localhost:8000` as its
  canonical URL when `APP_PUBLIC_URL` is unset.
- **The public pages sold capabilities the product does not have** *(public
  demo)*. The hero said it "summarizes long threads"; the marquee, the Triage card
  and the privacy policy said the same — and nothing generates a thread summary
  (threads are grouped and shown). "RAG" and "Semantic search" were chips on the
  landing page with no embedding, vector store or search behind them. The "typed
  tools" pillar described the benchmark's LLM agent: the product's own routing is
  deterministic code, and the model writes prose only, which is what the README
  has always said. The "retrieves the thread's history" pillar was untrue of the
  drafter, which is handed one message, the inferred signals, and up to three of
  the workspace's approved replies as a guide to voice. All rewritten to what the
  code does — including the meta description, which is what a pasted link
  unfurls to — and a test now names each retired phrase and why it is off limits
  *until the thing exists*. Each of the seven phrases was in the committed copy.
- **`/docs` described a different product.** It called the service "a
  deterministic, RL-style executive inbox simulation", and listed 46 of its 67
  endpoints under one untagged heading with the product's five groups lost among
  them. Product first, benchmark second, every route tagged, and a test that
  fails on an untagged one. (Routes added through `include_router` are not
  flattened into `app.routes` in this FastAPI version, so a post-hoc loop cannot
  see them — six dashboard endpoints stayed untagged until they were tagged at
  the include site.)
- **Every SQLite commit was an `fsync`.** The engine ran on SQLite's defaults —
  rollback journal, `synchronous=FULL` — while the repository layer commits once
  per call. Building one demo workspace issues about 260 commits, which was 3.7s;
  with WAL and `synchronous=NORMAL` it is 0.8s, and the background worker's
  writes no longer lock out request threads. Attached for the SQLite dialect
  only, and tested to be absent from a Postgres engine.
- **Traces could not be tied to a build.** OpenTelemetry's `service.version` was a
  hardcoded `1.0.0` while the service was at 1.1.0, and the module claimed a
  `MeterProvider` that was never configured. It is tracing only; metrics are
  Prometheus. Said so, and stamped the real version.
- **Documentation that contradicted the repo.** `SECURITY.md` said "the frontend
  runs eslint/prettier/type-check/build" (there is no frontend; the React
  dashboard was removed), and its CodeQL line, `CONTRIBUTING.md`'s eslint
  reference, two issue-template options, and a CI-jobs list in
  `TECHNICAL_REFERENCE.md` said the same. `DEPLOYMENT_GUIDE.md` claimed signup was
  off and the blueprint's Postgres was free — `render.yaml` says on and paid.
  `requirements.txt` said `render.yaml` sets the OTLP endpoint; it did not.
  `ROADMAP.md` listed deleted React work as open and a finished accessibility
  pass as "still open". The README's hand-typed demo numbers (50 messages, 11
  actions, one flagged draft) disagreed with the product (51, 12, two).
- **A test that could never pass.** `test_inbox_lists_the_messages` asserted
  `"Copilot" not in response.text` on a page whose `<title>` and sidebar logo
  both say "Copilot", and its pair asserted the word's *presence*, proving
  nothing. Both now assert the reader panel's own marker.
- **A revoked mailbox stayed marked healthy, silently, forever.** The pre-launch
  pass made a broken mailbox impossible to miss — a banner on every page, one
  email to the admins, an audit row — and then only ever reached that code from
  the two credential checks in `build_provider`. The common failure is not a
  missing token, it is a *refused refresh*: the customer removes the app, an
  administrator revokes it, or a Google consent screen left in "Testing" hits
  its seven-day expiry. That happens inside a provider call, hours or days after
  connecting, and `OAuthExchangeError` escaped everything — 500-ing "Sync now",
  and in the background worker landing in the catch-all that logs a stack trace
  and backs off. The connection stayed `connected`, so no banner, no email,
  nothing in the audit log, and the worker re-attempting a dead token every
  interval forever. The only symptom was an inbox that had stopped filling,
  which is exactly what a quiet week looks like. The refresher now flags the
  connection, which everything downstream already knew how to handle: the page
  says reconnect instead of 500-ing, the admins are told once, and the mailbox
  drops out of the sweep until a human fixes it. Only on an actual *refusal*,
  though — `OAuthExchangeError` now carries the provider's status code, and a
  4xx is the end of the mailbox while an unreachable endpoint or a 5xx is
  somebody else's thirty-second outage. Flagging that one would email a customer
  "reconnect your mailbox" over a DNS blip, and stop the worker from picking it
  back up on its own. Found by the integration layer above, which is the entire
  reason it exists.
- **Every approved Gmail reply arrived as a brand-new conversation.** `threadId`
  threads the *sender's* Gmail; the recipient's client threads by
  `In-Reply-To`/`References`, which the hand-built raw message never carried —
  at the exact moment the product is supposed to look like the executive wrote
  it. Replies also went to `From` even when the sender set `Reply-To`, which
  ticket systems and no-reply senders do constantly. Built with `EmailMessage`
  now, so a non-ASCII subject is encoded per RFC 2047 instead of being bare
  UTF-8 in a header.
- **Filing an Outlook message deleted the categories already on it.** Graph has
  no labels, so triage uses categories — but `categories` is a collection a
  PATCH *replaces*, and the write sent only ours. Every category the customer
  had filed that message under disappeared, in their own mailbox, as a side
  effect of us reading it. It also discarded our own: a message downgraded to
  `deferred` and then classified in the same sweep kept whichever write landed
  second. Gmail's `addLabelIds` is additive; Graph now matches it, at the cost
  of one GET per labelled message (Graph has no add-to-collection verb).
- **The first sync ran inside the OAuth redirect.** At the default
  `inbox_sync_limit` of 100, Gmail alone is one `messages.list` plus a hundred
  sequential `messages.get`, and with drafting on every held action adds two
  model calls — minutes of work inside an HTTP redirect, so the customer's first
  act after granting consent returned a proxy timeout. Now a `BackgroundTasks`
  job; the demo path stays inline, because feeling instant is the whole point of
  it. Nothing needs to recover a dead task: `last_synced_at` is still `NULL` and
  `BackgroundSyncWorker.is_due()` already treats a never-synced connection as
  due.
- **Graph was asked for `$top` with no `$orderby`.** Gmail's list endpoint
  documents reverse-chronological order; Graph's does not. Since a mailbox is
  capped per sweep, an oldest-first default would have synced mail from years
  ago on every pass and never surfaced the message someone was waiting on —
  while looking like it worked.
- **The connect page claimed "read-only access to your inbox"** while the app
  requests `gmail.modify`, which writes labels. Untrue to the customer, and
  contradicting the scope table on `/privacy` is a documented way to fail an
  OAuth review.
- **The release workflow's notes step depended on an unspecified `awk` escape.**

### Changed

- **The shared demo account is now optional.** It still exists — `make demo`
  seeds it, its login still prefills, and it is still fenced from every
  administrative action — but nothing on the landing page depends on it.
  `DEMO_SEED_ON_STARTUP` in `render.yaml` is kept and marked optional.
- **Schema version 9** adds the nullable `saas_organizations.sandbox_expires_at`,
  which is how the purge, the worker and the guard all agree on what a sandbox
  is, in place of a naming convention on the owner's address. `NULL` on every
  existing row, and a migration test proves that a pre-existing customer's
  organization comes through it as a non-sandbox, because the alternative is the
  first sweep erasing them.
- **`FixedWindowRateLimiter` takes its window at construction**, not per call,
  so the demo limiter can count over ten minutes while every existing caller keeps
  its one-minute window and the `allow(key, limit)` signature a test fixture
  monkeypatches.
- **The accessibility sweep and the 320px reflow test now cover `/demo` and a real
  sandbox session** — the banner on every page and the tour card — and the reflow
  test reaches the sandbox by clicking the landing page's button in a real browser
  at phone width, in both themes.
- **One canonical list of escalation roles** (`app.core.models.ESCALATION_ROLES`,
  with `escalation_role_for`). Six places spelled it out for themselves — the
  baseline policy, the LLM policy, the agent's guardrail and its validator, and
  the tool schema — and the settings page has to agree with all of them, or a
  workspace configures a role nothing ever emits.
- The demo workspace seeds its own escalation contacts. Without them every
  escalation in the demo queue carried a "no mailbox set" warning: true about an
  unfinished setup, false about the product.
- The unavailable-provider card said "An operator must set this server's OAuth
  client id and secret" — addressed to whoever runs the deployment, shown to
  whoever is trying to use it. On a self-serve signup those are never the same
  person, and it reads as a broken product.
- **`docs/OAUTH_SETUP.md` and `LAUNCH_CHECKLIST.md`: onboard with the consent
  screen "In production", not "Testing".** Both said "100 test users, each
  seeing an unverified-app warning" and left it there. In Testing, Google
  expires refresh tokens after **seven days** — so a client connected that way
  stops syncing weekly and must reconnect by hand, and "100 users of runway"
  is really zero. "In production" while still unverified keeps the cap and the
  warning and removes the clock.

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
  ceiling) caps it. It covers **both** paid calls a held action makes — writing
  the draft and verifying it; billing only the first would have left about half
  this feature's spend outside the ledger. At the cap, drafts fall back to
  rule-based prose and verification to its deterministic checks, which is
  exactly where a deployment with no API key already lives; triage, commitment
  tracking and sending are unaffected. Surfaced in Settings.
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
