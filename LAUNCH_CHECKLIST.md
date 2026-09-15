# Launch checklist

Everything here needs a person, a credit card, or a queue. None of it can be
done by another coding session — that work is finished and merged.

They are in the order they unblock each other. Item 1 has no waiting time and
gives you a working provider the same day; item 4 has a lead time nobody
controls, so start it early and then forget about it.

---

## 1. Register the Microsoft 365 app — **do this first**

It has no review queue. You can have a real mailbox connected today.

`docs/OAUTH_SETUP.md:20-42` has the steps. Two things that catch people out:

- `Mail.ReadWrite` and `Mail.Send` trigger **admin consent** in most managed
  tenants. Have the admin-consent URL ready before a rollout call, or the
  prospect's IT will stop the demo dead.
- `MICROSOFT_OAUTH_TENANT` is set to `common` in `render.yaml`, which is what
  you want for any work or school account. Change it only for a single-tenant
  deployment.

## 2. Deploy to Render

`render.yaml` is a Blueprint: Render → New → Blueprint → point it at the repo.

**Back up `AUTH_SECRET_KEY` the moment it is generated** (dashboard →
Environment → copy it somewhere you will still have in a year). It signs every
session token, every licence key, every CSRF token, *and* it derives the
encryption key for stored mailbox tokens. Losing it invalidates every licence
key you have ever issued to a customer and makes every stored OAuth token
permanently unreadable — the product handles this gracefully (it flags the
mailbox "needs reconnect" rather than 500ing) but every customer has to
reconnect, and every key has to be reissued.

Both services are on paid plans deliberately. Render's **free Postgres is
deleted after ~30 days**, and a **free web service sleeps** — a sleeping
instance runs no background sync, so the approval queue never fills on its own
and the product looks like it does nothing.

Two settings worth a second look before you deploy:

| | |
|---|---|
| `LLM_MONTHLY_BUDGET_USD` | Defaults to 25 per workspace per calendar month. This is the only thing between one large mailbox and an unbounded OpenAI bill. It covers **both** paid calls a held action makes — writing the draft and verifying it. Reaching it degrades drafts to rule-based prose and verification to its deterministic checks; triage and sending keep working. `0` removes the ceiling — don't, until the bill is a line item somebody watches. |
| `SYNC_WORKER_INTERVAL_SECONDS` | 900 (15 min). Lower means fresher queues and more provider API calls. |

**A second, separate source of model spend:** if you set `OPENAI_API_KEY` as a
**GitHub repository secret**, the nightly `Draft quality` workflow runs an
LLM judge over the 11 committed demo drafts. That is your CI spending your key,
not a customer's workspace, so `LLM_MONTHLY_BUDGET_USD` does not govern it — it
is a few cents a night, and the workflow skips the judge entirely when no key is
configured. Mentioned only so it is not a surprise line on the invoice.

## 3. SMTP credentials

**Not optional.** With `EMAIL_PROVIDER` unset or misconfigured, the sender
falls back to `console`: password-reset links, member invite passwords and
"your mailbox stopped syncing" notices are written to the log and never
delivered — while the UI tells the user a mail was sent.

Set `EMAIL_FROM`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` in the
Render dashboard. Then actually trigger a password reset and confirm it lands.

## 4. Gmail: OAuth client → verification → CASA Tier 2

**The only item with a lead time you do not control: 6–12 weeks.** Start it as
soon as the app is deployed, then stop thinking about it.

The blocker is already cleared: `/privacy` exists on the app's own domain with
the Google Limited Use disclosure and a per-scope justification table.
`gmail.readonly` was dropped (redundant with `gmail.modify`, and requesting
more than you use is a documented rejection reason), so two Google scopes
remain — `gmail.modify` and `gmail.compose`. Both are **restricted**, so
verification plus a CASA Tier 2 assessment applies: roughly **$540–$1,000** on
the self-serve lab path, **redone every 12 months**. There is no scope
arrangement that avoids it. The only escape is not reading the mailbox, which
is the product.

Order: create the Google Cloud project → create the OAuth client → enable the
Gmail API → add your first test users (Gmail allows 100 immediately, each
seeing an "unverified app" warning) → submit for verification → book the lab.

**Two things to settle before you submit:**

- **Have a lawyer read `/privacy` and `/terms`.** They are a contract with your
  users and a submission to Google. What is in the repo is a solid draft, not a
  sign-off.
- **Verify the Limited Use claim against your own provider account.** The
  policy states that mailbox data is not used to develop, improve, or train
  generalized AI/ML models. That is true of the OpenAI API's default terms, but
  it is a claim *you* are making under review. Confirm your account settings
  rather than trusting a file.

## 5. The end-to-end test that actually matters

On the deployed instance, with a real mailbox:

1. Sign up cold, as a stranger would.
2. Connect a real Gmail account **and** a real Microsoft 365 account.
3. **Wait for the background worker. Do not click Sync.** Clicking Sync tests a
   button; waiting tests the product. If the queue fills on its own, the thing
   works.
4. Approve a drafted reply. Confirm it arrives in the recipient's inbox.
5. Confirm `/app/activity` logged it, with an IP.
6. Check `/app/waiting` found the promises in that mail.
7. Trigger a password reset and confirm the email lands (this is the real test
   of item 3).
8. Open `/operator` and confirm it shows the workspace, the mailbox, the
   worker's last pass, and the month's model spend.

## 6. Watch it for a week

`/operator` answers "is it working?" in one page — sign in with
`OPERATOR_TOKEN`. The four things it will tell you before a customer does:

- a mailbox that stopped syncing (the customer is emailed too, once)
- the background worker gone stale (nothing is being triaged on its own)
- replies a human approved that the provider refused
- a workspace approaching its model budget, or its trial expiry

---

## Already done, so you don't go looking

- Deployment config, production flags, release workflow paths — Phase 0.
- Self-serve signup, no pricing page anywhere — Phase 1.
- `/privacy` and `/terms`, the Gmail CASA gate — Phase 1.5.
- Landing page claims rendered from a CI-verified artifact — Phase 2.
- The signed-in app: bodies, threads, search, filters, paging, keyboard nav — Phase 3.
- Claim-level verification evidence — Phase 4.
- "Waiting on" commitment tracking — Phase 5.
- Dead code removed, mypy a real gate, Helm chart installable — Phase 6.
- Model spend ledger and monthly cap, broken-mailbox alerting, worker
  heartbeat, the `/operator` health page, and a full accessibility and 320px
  reflow pass — the pre-launch hardening pass.
