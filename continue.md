# Continue here

**Last session:** 2026-09-15. **Branch:** `security-scan-green`.
**Human-gated work:** [`LAUNCH_CHECKLIST.md`](LAUNCH_CHECKLIST.md) — start there.

The 2026-08-31 launch pass (phases 0–6) is **merged to `main`** via PRs #5 and
#6. On top of it, a pre-launch hardening pass closed the gaps that only appear
once the thing is deployed with real money and real mailboxes attached.

---

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
- **`psycopg` is not installed locally**, so anything Postgres-flavoured fails
  at driver import rather than at connect. CI's `test-postgres` job is the real
  check.
- **A long-lived dev database may predate Phase 6.** `create_all` adds columns
  but never drops them, so a `data/*.db` from before the `Organization.status`
  removal will fail inserts with `NOT NULL constraint failed`. Delete it; it is
  an output, recreated on demand.
- Run the app: `uvicorn app.main:app --port 8000`. Seed it: `make demo`.
