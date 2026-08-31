# Continue here

**Last session:** 2026-08-31. **Phases 0–6 are all landed, committed, and pushed.**
**Full plan:** `~/.claude/plans/radiant-leaping-tarjan.md` (kept for reference; the
work in it is done)
**Branch:** `security-scan-green` — 16 commits ahead of `main`, pushed, CI running.

1020 tests pass · ruff clean · mypy clean · Helm chart verified against helm 3.16.3.

---

## What is left, and none of it is code

Everything below needs a person, a credit card, or a queue. There is no
remaining task in this repo that another coding session would advance.

### 1. Decisions you owe before deploying

| | |
|---|---|
| **Render plans** | `render.yaml` sets `plan: starter` (web) and `basic-256mb` (Postgres). Both cost money. Free web sleeps — a sleeping instance runs no background sync, so the approval queue never fills on its own — and free Postgres is deleted after ~30 days. |
| **`AUTH_SECRET_KEY`** | Render generates it. **Back it up immediately** (dashboard → Environment). It signs every session and license key *and* derives the mailbox-token encryption key. Losing it invalidates every key you have ever issued and every stored mailbox token. |
| **SMTP** | Not optional. Without it, password resets and member invites are written to the log while the UI says they were sent. |
| **OAuth credentials** | Gmail and Microsoft 365 both need real client IDs/secrets set in the Render dashboard. |

### 2. Microsoft 365 first — it needs no review

`docs/OAUTH_SETUP.md:20-42`. Register the app today; it unblocks a real
provider immediately. Note `Mail.ReadWrite`/`Mail.Send` trip admin consent in
most managed tenants — have the admin-consent URL ready for rollout calls.

### 3. Gmail CASA — start the queue, then forget about it

The only item with a lead time you do not control: 6–12 weeks, first
submission to approval.

The blocker is cleared. `/privacy` exists, on the app's own domain, carrying
the Google Limited Use disclosure and a per-scope justification table.
`gmail.readonly` was dropped (redundant with `gmail.modify`, and requesting
more than you use is a documented rejection reason), so the two remaining
Google scopes are `gmail.modify` and `gmail.compose`. Both are *restricted*, so
verification plus a CASA Tier 2 assessment applies — roughly $540–$1,000 on the
self-serve lab path, redone every 12 months. There is no scope arrangement that
avoids it; the only escape is not reading the mailbox, which is the product.

Order: create the Google Cloud project, OAuth client, enable the Gmail API, add
your first test users (Gmail works for 100 of them immediately, each seeing an
"unverified app" warning), then submit and book the lab.

**Two things to check before you submit:**

- **Have a lawyer read `/privacy` and `/terms`.** They are a contract with your
  users and a submission to Google. What is in the repo is a solid draft, not a
  sign-off.
- **Verify the Limited Use claim against your provider account.** The policy
  states that mailbox data is not used to develop, improve, or train
  generalized AI/ML models. That is true of the OpenAI API's default terms, but
  it is a claim *you* are making under review — confirm your account settings
  rather than trusting a file.

### 4. The two verifications that still need a machine

- **`docker build` from a clean clone** → landing page renders with zero 404s.
  Docker is not installed on this machine, so this was never run locally; CI's
  `docker` job covers the build and smoke-tests `/`, `/login` and `/docs`.
- **Push a `v0.1.1` tag** → the release workflow goes green. Its paths were
  fixed in Phase 0 (they pointed at four directories deleted in the `env/` →
  `app/` rename, so no tagged release could ever have shipped), but no tag has
  been pushed since.

### 5. The only test that really matters

End to end on a real mailbox: deploy to Render → sign up cold → connect a real
Gmail account **and** a real Microsoft 365 account → **wait for the background
worker, do not click Sync** → confirm the approval queue filled on its own →
approve a drafted reply → confirm it arrives → confirm `/app/activity` logged
it, with an IP → check `/app/waiting` found the promises in that mail →
trigger a password reset and confirm the email actually lands.

---

## What shipped, in one line each

| Phase | Commit |
|---|---|
| 0 | `72588cd` A clean checkout deploys: assets committed, prod flags on, release gate fixed, tests off your real DB, migration out of import, alerts able to fire |
| — | `b860a7c` Landing v3 (the redesign that was uncommitted when you went to sleep) |
| 1 + 1.5 | `31de916` Self-serve motion, no pricing anywhere, `/privacy` + `/terms` |
| 2 | `4c7280a` The proof section is measured, from an artifact CI re-verifies |
| 3 | `ffde5af` The signed-in app became somewhere you could work |
| 4 | `c7ef395` Verification as the product: claim-level evidence, not a chip |
| 5 | `64eb5e6` "Waiting on": commitment tracking in both directions |
| 6 | `fb58efe` Dead code deleted, gates made real, Helm chart made installable |

### Things worth knowing that were not in the plan

- **The demo mailbox had no threads at all** — 50 messages, 50 distinct thread
  ids — while the landing page sold "summarizes long threads". It now has a
  genuine follow-up in the datacentre incident, and the inbox groups it.
- **The commitment extractor pulled two promises out of spam** on its first run
  over the demo mailbox ("subscribe now and we will register your team"). Spam
  is now excluded using the copilot's own classification. Two rows of noise in
  a seven-row follow-up list is enough that nobody opens it twice.
- **The inbox summary tiles followed the filter**, so filtering to spam showed
  "8 triaged" — a false statement about what the copilot did, as the first
  number on the page. They now describe the workspace regardless of the filter.
- **The Helm chart's defaults, not an edge case, were the unsafe configuration.**
  It shipped two replicas of an un-lockable background worker against per-pod
  SQLite files, signing sessions with a constant published in this repo.
- **`User.status` was kept** despite the plan listing it for deletion:
  `count_active_for_org` reads it to enforce seat limits, so removing it would
  have broken seat enforcement to delete a comment. `Organization.status` was
  genuinely dead and is gone.

### New commands worth knowing

```bash
python scripts/build_landing_metrics.py          # regenerate the landing artifact
python scripts/build_landing_metrics.py --check  # what CI runs; writes nothing
python scripts/capture_screenshots.py            # re-photograph the real product
python scripts/optimize_images.py                # regenerate the WebP variants
```

Run the last two together after any visible change to the inbox or approvals —
the landing page says "Real product. Real inbox. No mockups.", and that only
stays true if the screenshots keep up.

---

## Environment notes

- **Docker is not installed on this machine.** CI covers the container build.
- **Helm is not installed either**, but the chart was verified with a
  downloaded `helm 3.16.3`: `lint` passes, a valid config renders, and all four
  unsafe configurations are refused.
- **`psycopg` is not installed locally**, so anything Postgres-flavoured fails
  at driver import rather than at connect. CI's `test-postgres` job is the real
  check.
- Full suite: ~5.5 min (`python -m pytest -q`).
- Run the app: `uvicorn app.main:app --port 8000`.
