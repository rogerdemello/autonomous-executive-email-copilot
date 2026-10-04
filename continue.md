# Continue here

**Last session:** 2026-10-04. **Branch:** `main` — work directly on it now.
`security-scan-green` is spent: PR #7 was *squash*-merged, so that branch reads
as permanently ahead-and-behind. Do not try to catch it up.
**Human-gated work:** [`LAUNCH_CHECKLIST.md`](LAUNCH_CHECKLIST.md) — start there;
section 0 is new and takes ten minutes.

**2026-10-04 was a different kind of pass: use the deployed site as a stranger
would.** It found what the test suite structurally could not — the live "Try the
live demo" button led to a blank sign-in form, every public page published
`sales@example.com`, and the page layout broke for anyone with a cached
stylesheet. See [the public demo](#the-public-demo) below, and the `[Unreleased]`
section of `CHANGELOG.md` entries marked *public demo*. **Nothing from that pass
is committed or deployed yet** — see "State of the tree".

The 2026-08-31 launch pass (phases 0–6) is **merged to `main`** via PRs #5 and
#6. On top of it, a pre-launch hardening pass closed the gaps that only appear
once the thing is deployed with real money and real mailboxes attached; it is
tagged **v1.1.0**.

Everything since v1.1.0 is **unreleased** and is one single theme: *what breaks
the first time a real mailbox is attached.* See the `[Unreleased]` section of
`CHANGELOG.md` — HTML bodies, Gmail reply threading, Outlook categories,
escalations addressed to the wrong person, the first sync leaving the OAuth
redirect, Graph result ordering, the admin-consent wall. Every one of them was
green in the test suite, because the demo provider is in memory, its bodies are
plain text, its drafts are cached, and its mailbox is never actually written
to.

**That is no longer the only mailbox under test.** `tests/integration/` runs the
product against a stateful fake of the real provider APIs — see
[the integration layer](#the-integration-layer) below. It is where a provider
change belongs now, and it is what found the revoked-token bug described there.

Gates are green as of 2026-10-04: **1362 tests, 84.9% coverage**, ruff check and
format, mypy, bandit, the landing-metrics check, and the draft-quality gate (10/11).
The full suite with coverage takes ~15–25 minutes on this machine.

---

## The public demo

`POST /demo` builds a visitor a **private sandbox workspace** and signs them in —
`app/saas/sandbox.py`, pinned by `tests/test_demo_sandbox.py` (48 tests). It
replaced "a login form pre-filled with a shared account's password", which only
worked if that account had been seeded and which let the first visitor empty the
approval queue for everyone after.

What to know before touching it:

- **A sandbox is a real organization** with `saas_organizations.sandbox_expires_at`
  set (schema version 9). That one column is how the purge, the worker's
  exclusion in `list_all_connected`, the no-live-model rule in
  `InboxSyncService._sync`, and the guard in `deps.reject_shared_demo_account` all
  agree on what a sandbox is. Do not replace it with a convention on the owner's
  email. The `.invalid` address exists only so nothing addressed to it can be
  delivered (`email.is_undeliverable`).
- **The safety properties are the point, and one test originally failed to prove
  one of them.** `test_a_sandbox_never_reaches_the_model` passed with the protection
  *removed*, because a plain re-sync is idempotent and never reaches the drafting
  code; it has to reject a draft first so the next sync re-proposes it. Every
  property in there was checked by removing the code and watching the test fail —
  do that again if you change the mechanism.
- **It refuses at the cap; it never evicts.** `MAX_LIVE_SANDBOXES` returns a 503
  page rather than deleting someone's live demo to make room.
- **The limit is per-IP and the IP is the client's claim.** Uvicorn is started with
  `forwarded_allow_ips="*"`, which takes the leftmost `X-Forwarded-For` entry, and
  a client can prepend to it. The cap bounds the damage; the limit does not.
  Pre-existing, and the same for the login throttle.
- **Seeding was 3.7s per workspace, almost entirely SQLite `fsync`s** (264 commits).
  WAL + `synchronous=NORMAL` in `app/core/db.py` made it 0.8s, which is why
  there is no template-cloning machinery. Measure before building any.
- **`.tour`, `.tour__row` and `.tour__text` are the landing page's.** The sandbox's
  tour card is `.demo-tour`. And app.css gives every `<section>` 62px of padding
  and a top border, so a card that is a `<section>` looks broken.
- **Static assets are fingerprinted** (`app/web/assets.py`, `asset_url`). Templates
  that extend `base.html` need it registered on their Jinja environment — the
  operator console builds its own, and `register()` is called in both places.
- **Link previews need absolute URLs from the serving origin**
  (`marketing.public_base_url`), and `static/img/og-card.jpg` is a real screenshot:
  `python scripts/capture_screenshots.py --only og-card.jpg`.

## State of the tree

**Nothing from 2026-09-26 or 2026-10-04 is committed, pushed or deployed.** `HEAD` is
`5039ecf`; `git status` is ~65 modified files plus new ones. Two layers are mixed
in it, and they overlap in `CHANGELOG.md`, `continue.md`, `app/web/routes.py`,
`app/web/static/app.css`, `app/web/templates/inbox.html` and
`tests/test_web_pages.py`, so splitting them into two commits needs `git add -p`:

1. The real-mailbox pass: `oauth.py`, `provider_factory.py`, the inbox reader,
   `tests/integration/`.
2. The public-demo pass: everything under "The public demo" above, plus the
   erasure fix, the placeholder-email fix, fingerprinted assets, link previews,
   the corrected landing copy, and the docs.

**Pushing to `main` deploys** — `render.yaml` has `autoDeploy: true` — and the live
service is not the one this blueprint describes (LAUNCH_CHECKLIST section 0). After a
deploy, press the button from a private window before telling anyone.

New files to remember are untracked: `app/saas/sandbox.py`, `app/web/assets.py`,
`app/web/templates/{_contact,_tour,demo}.html`, `app/web/static/img/{og-card.jpg,
product-flagged.png}`, `.github/workflows/keepalive.yml`, `tests/test_demo_sandbox.py`,
`tests/integration/`.

## What the hardening pass changed, and why it mattered

Each of these was a way the product could fail *silently* in production — the
worst kind, because the symptom is nothing happening.

| | The gap | Now |
|---|---|---|
| **Model spend** | Cost went to an in-process Prometheus counter: not per-org, reset by every deploy. Drafting is on in production and the worker sweeps every mailbox every 15 minutes, so there was no ceiling and no record. | `saas_llm_usage` bills every call to the org that caused it. `LLM_MONTHLY_BUDGET_USD` (default 25) caps it. At the cap, drafts fall back to rule-based prose — triage, verification and sending are untouched. Shown in Settings and `/operator`. |
| **A revoked mailbox token** | Flipped the connection to `status="error"`, which was correct — and visible only as a chip on `/app/connect`, a page nobody opens twice. Everywhere else the inbox just stopped filling, which looks like a quiet week. | A banner on every signed-in page, one email to the admins on the transition, and an audit row. |
| **A dead background worker** | Logged each pass and dropped it. `/health/ready` still returned 200. | Heartbeat with the last ten passes; reported by `/health/ready` and `/operator`. Deliberately **not** a 503 — the web tier is still fine, and cycling the instance would take the product down to fix a cron. |
| **"Is it working?"** | Answerable only over seven JSON endpoints with a bearer token, from a shell. | `/operator`, one page. Sign in with `OPERATOR_TOKEN`. |
| **The app on a phone** | Rendered **620px wide inside a 320px viewport** — every signed-in page, both themes. | Zero horizontal scroll across 13 pages in both themes, pinned by a test. |

---

## The state of the gates

```bash
python -m pytest -q                              # full suite
ruff check . && ruff format --check .
mypy app --config-file mypy.ini                  # a real gate since Phase 6
python scripts/build_landing_metrics.py --check  # CI's landing-claims check
```

`tests/test_web_reflow.py` needs Playwright and skips without it, exactly like
`scripts/capture_screenshots.py`. Run it if you touch `app.css`.

The full suite takes about **12 minutes** on this machine; `tests/integration/`
is about 2 of them (55 tests, each signing up a workspace and syncing a mailbox
over HTTP). Run just that package while working on a provider:

```bash
python -m pytest tests/integration -q
```

---

## The integration layer

`tests/integration/` is the answer to why seven production-only bugs were all
green. It runs the shipping path — the OAuth callback, `build_provider`
decrypting the stored token, the real `GmailProvider`/`MicrosoftGraphProvider`,
`InboxSyncService`, the signed-in HTML app — against a **stateful fake of each
provider's HTTP API** in `tests/integration/wire.py`.

The seam is `httpx` itself: `install()` patches `httpx.request` and `httpx.post`,
so the code under test includes `_httpx_transport`, the `Authorization` header it
builds, and its handling of a 202 with no body. The providers' own `transport=`
argument is deliberately *not* used — `tests/test_gmail_provider.py` covers that
level, and going through httpx is the difference between this and a second unit
test. (`TestClient` is an `httpx.Client` subclass and calls its own bound
methods, so the app's own requests are untouched.)

It is a mailbox, not a stub, and each property is there because its absence hid a
bug:

- writes change what later reads return — which is what makes "a second sync
  proposes nothing and writes nothing" statable at all;
- a Gmail label must exist before it can be applied, and a duplicate `POST
  /labels` is a 409;
- a Graph `categories` PATCH **replaces** the collection, because it does;
- a Gmail send carrying a `threadId` but no `In-Reply-To` is accepted and
  recorded as `threaded=False` — the recipient's client is the thing that has to
  thread it;
- a `$orderby`-less Graph list answers **oldest-first**, since Graph documents no
  default and a capped sweep that takes the wrong end syncs someone's 2019;
- an expired access token is a 401 until the refresh token is spent; Google keeps
  its refresh token and Microsoft rotates it, so both branches of the refresher
  are real;
- a URL no wire serves is a 404 **and** a recorded failure — the `connect`
  fixture asserts that log is empty, so a wrong URL can never read as an empty
  mailbox.

**When you touch a provider, teach the wire the route and assert on the mailbox,
not on the call.** The fixtures give you a workspace signed up over the signup
form and a mailbox attached through the app's own connect flow, so a test reads
as "connect, wait, then look at what arrived":

```python
def test_something(self, workspace, connect):
    wire = google_wire()
    connect(wire, "google")                    # the first sync has already run
    workspace.approve(workspace.held("reply")["id"])
    assert wire.sent[0].to == "..."            # what actually left the mailbox
```

Because `_first_sync` swallows its exception on purpose, a broken sync shows up
as an empty inbox. The `connect` fixture asserts `last_synced_at` was set and
says so; to see the traceback, call `InboxSyncService().sync(...)` directly.

The layer was checked by re-introducing each of the seven provider fixes in this
release by hand. All seven fail — if you change the wire, do that again for
whatever you are relying on.

---

## Onboarding a real client mailbox

The Gmail and Microsoft 365 integrations are **built** — OAuth, encrypted
tokens, refresh-on-401, fetch/reply/draft/label/archive, the connect UI, and
background sync. What gates a real client is credentials and Google's queue,
not code. See `LAUNCH_CHECKLIST.md` and `docs/OAUTH_SETUP.md`.

- **The first sync never runs in the OAuth callback.** It is a
  `BackgroundTasks` job. Inline, a real mailbox (100 messages × a sequential
  Gmail fetch, plus two model calls per held action) took minutes inside an
  HTTP redirect and timed out the proxy at the highest-trust moment in the
  product. The demo path *is* still inline, and should stay that way.
- If that background task dies, nothing needs to recover it: `last_synced_at`
  is still `NULL` and `BackgroundSyncWorker.is_due()` treats a never-synced
  connection as immediately due.
- **`/app/inbox` has three empty states**, not two: no mailbox, *reading your
  mailbox* (self-refreshing, the normal view for the first minutes of a real
  account), and synced-but-empty.
- **Admin consent is the expected first outcome on Microsoft**, not an error.
  `Mail.ReadWrite`/`Mail.Send` need tenant-admin approval in most managed
  directories, and Microsoft reports it as `access_denied` — the same code as
  a user clicking Cancel. `oauth.needs_admin_consent()` tells them apart and
  the callback renders the adminconsent URL to hand to their IT.

### Growing to several mailboxes per client — the seam already exists

Today every member of a workspace sees every connected mailbox's mail, which is
correct for the current ICP (a solo exec, or an exec plus an assistant who is
*meant* to see it). It is wrong the moment one client has several people each
connecting their own inbox.

That change is a `WHERE` clause, not a migration:

- `ProcessedMessage` **already carries `connection_id`** (`models_db.py:240`,
  part of the `uq_processed_message` constraint), as does `ProposedAction`.
- The inbox reads through exactly one chokepoint,
  `ProcessedMessageRepository.list_for_org` (`repository.py:679`); approvals
  mirror it at `repository.py:833`. Both filter on `org_id` alone.

So: add an access rule (who may see which connection) and a filter in those two
methods. No speculative parameters have been added for it — don't add any until
the feature is real.

## Things worth knowing before you change something

- **`overflow-x: auto` does not make a grid or flex child shrink.** Its
  automatic minimum size is min-content. This single fact, in three different
  guises (`1fr`, an `overflow-x: auto` nav, `minmax(300px, 1fr)`), is why the
  entire signed-in app was 620px wide on a phone. Use `minmax(0, 1fr)`,
  `min-width: 0`, and `minmax(min(300px, 100%), 1fr)`.
- **A `position: absolute` element escapes a scroll container** that is not in
  its containing-block chain. `.visually-hidden` labels inside tables were
  widening the whole document until `.table-scroll` got `position: relative`.
- **`MailboxRepository.set_status` returns whether the status *changed*,** not
  whether the row was found. The broken-mailbox notification depends on it: the
  worker re-derives that state every sweep, and 96 mails a day about one dead
  token is how a notification becomes a filter rule.
- **Every way a mailbox can die has to reach `_mark_broken`.** Building the
  alerting was not the same as wiring it up: for a while it was only reachable
  from the two credential checks in `build_provider`, and the *common* failure —
  the provider refusing a refresh — happened later, inside a provider call, and
  escaped as an `OAuthExchangeError` that 500'd "Sync now" and landed in the
  worker's catch-all. The connection stayed `connected`, so none of the
  machinery fired. `_make_refresher` now flags it — but **only on a refusal**:
  `OAuthExchangeError.is_refusal()` is true for a 4xx and false for an
  unreachable endpoint or a 5xx, because flagging the second kind emails a
  customer about a mailbox that is fine and takes it out of the sweep that would
  have recovered it by itself. If you add another way to authenticate, ask what
  marks the connection when it stops working — and what must not.
- **One broken mailbox stops `POST /app/sync` for the others.** The JSON API
  (`processing_routes.sync`) deliberately reports a broken connection and carries
  on; the web `sync_all` calls `_sync_connection` per connection and lets its
  `HTTPException` out, so the first broken mailbox aborts the loop and any
  mailbox after it is not synced. The background worker is unaffected — it is the
  path that matters, and `tests/integration/test_two_mailboxes.py` pins that
  connections do not share fate there — so this is a wrong button, not a broken
  product. Left as it is because "make the button quiet about a mailbox the
  banner is already shouting about" is a product call, not a bug fix.
- **`escalate_to` is a role, not an address, and it must stay that way.**
  `legal_team` / `chief_of_staff` (`app.core.models.ESCALATION_ROLES`) is a
  judgement about the mail; who holds legal is a fact about the workspace, and
  lives in `saas_escalation_contacts`. Keep the policy and the model naming
  roles — letting either emit an address would put "who do we tell" inside the
  model's reach. The provider seam is
  `create_escalation_draft(message, body, *, to)`: the recipient is a required
  keyword precisely so the old bug (a "draft" that was a reply to the outside
  sender) cannot come back by omission. An unset role must keep failing the
  action rather than falling back to anyone.
  (`TeamSettings.escalation_targets` in `app/core/db.py` is unrelated and
  dormant — nothing in the SaaS path reads it.)
- **The drafter is deliberately org-unaware.** It is a pure prose function; the
  caller knows whose bill it is and writes the ledger row. Don't hand it a
  tenant — that would make it one more place that could leak across one.
- **Screenshots are load-bearing.** The landing page says "Real product. Real
  inbox. No mockups." After any visible change to the inbox or approvals:
  ```bash
  python scripts/capture_screenshots.py && python scripts/optimize_images.py
  ```
- **The demo mailbox is content, not theatre.** Its routing is computed by the
  same `BaselinePolicy` that runs against a real Gmail account. Editing a
  subject line in `data/demo/inbox.json` genuinely changes the decision.

---

## Environment notes

- **Docker is not installed on this machine.** CI's `docker` job covers the
  build and smoke-tests `/`, `/login` and `/docs`.
- **Helm is not installed either**, but the chart was verified with a
  downloaded `helm 3.16.3`: `lint` passes, a valid config renders, and all four
  unsafe configurations are refused.
- **`psycopg` 3.3.4 *is* installed locally as of 2026-09-26** (it is pinned in
  `requirements.txt`, despite the "optional dependency" comment at
  `app/core/config.py:70`). So Postgres-flavoured code now fails at *connect*,
  not at driver import — a different and much later failure. No test is gated on
  it (`tests/test_db_engine.py` only asserts URL normalisation), so CI's
  `test-postgres` job against a real server is still the only real check.
- **`pip install -r requirements.txt` here installs into the global
  interpreter**, `AppData\Local\Programs\Python\Python312`, even with `(.venv)`
  showing in the prompt — that is also the interpreter `python -m pytest` uses,
  so the suite does test what was installed. The cost is that unrelated
  projects' packages share the directory: the `supabase`/`postgrest`/`storage3`
  conflicts pip reports against `httpx==0.28.1` come from those, not from this
  project, and nothing here imports them.
- **A long-lived dev database may predate Phase 6.** `create_all` adds columns
  but never drops them, so a `data/*.db` from before the `Organization.status`
  removal will fail inserts with `NOT NULL constraint failed`. Delete it; it is
  an output, recreated on demand.
- Run the app: `uvicorn app.main:app --port 8000`. Seed it: `make demo`.
