# Demo walkthrough

A script for showing this to someone in about eight minutes, and an honest
account of what is real and what is simulated — so you are never caught out by
the follow-up question.

## Before you start

```bash
pip install -r requirements.txt
make demo                          # or: python scripts/seed_demo.py
uvicorn app.main:app --port 8000
```

`make demo` is idempotent — run it again between rehearsals to reset the
workspace to a clean state. `python scripts/seed_demo.py --fresh` deletes the
organization and rebuilds it from scratch.

**No network, no API key, no OAuth credentials.** If the venue's wifi fails, the
demo still runs. The output is identical every time.

### Once, before the first rehearsal: generate the drafts

```bash
python scripts/seed_demo.py --fresh --with-llm     # needs a key and a network
```

This calls the configured model to write the reply and escalation prose and
commits it to `data/demo/drafts.json`. **Run it once.** Every later run — and the
demo itself — replays those drafts from disk, so what a judge reads is genuine
model output produced with no network at the venue.

The seeder tells you where the prose came from:

```
Triaged 50 messages: 81 applied automatically, 11 held for approval
  Drafts: 11 model-written
```

If it says `authored fixture prose` or `the policy's generic sentence` instead,
the cache is empty — the demo still works, it just falls back to the written
fixtures. Check this line before you present.

Sign in with `alex.chen@northwind.example` / `demo1234`. The login page shows
these credentials automatically whenever the demo account exists.

---

## The walkthrough

### 1. The landing page — `/`

> "This is the product. An email copilot for executives: it triages the inbox,
> drafts what's worth sending, and routes legal and security matters to the right
> owner — but it never sends anything without a human."

Scroll to **Proof**. The benchmark table is real, measured output, including the
unflattering cell.

> "These numbers come from a reproducible benchmark that ships in the repo. Note
> the frontier model scores 0.17 on narrow classification — worse than a
> heuristic. We publish that rather than hide it, because it's an
> agent-design finding: its guardrails trade coverage for caution."

### 2. Pricing — `/pricing`

> "Sales-led. No card capture. The plans here are generated from the same
> licensing registry that grants entitlements at runtime, so what a customer
> is shown and what they actually get cannot drift apart."

### 3. Sign in — `/login`

Use the demo credentials shown on the page.

If someone asks about SSO: the **Sign in with SSO** button appears when `OIDC_ISSUER`,
`OIDC_CLIENT_ID`, and `OIDC_CLIENT_SECRET` are configured. The flow does real
RS256 verification of the id_token against the issuer's published JWKS.

### 4. Connect a mailbox — `/app/connect`

Three cards: Gmail, Microsoft 365, and the demo mailbox.

> "Gmail and Microsoft 365 are real OAuth connections — this server just doesn't
> have client credentials configured, so they show as unavailable rather than
> failing when you click them. For the demo I'll use the built-in mailbox."

Click **Use the demo mailbox**. This creates a mailbox connection and immediately
runs a sync — the same code path a real Gmail connection takes. Only the provider
differs.

### 5. The inbox — `/app/inbox`

Fifty messages in a plausible COO's morning. Start with the summary bar at the
top, because it is the whole argument in one line:

> **50 triaged · 81 applied automatically · 11 need you**
>
> "Fifty messages arrived. Eleven need the COO. That ratio is the product —
> everything else is how it earns the right to claim it."

The list below is ranked the way the copilot ranked it, with priority and risk
chips. Click through four, in this order:

**The contract (Rachel Okafor, 07:12)** — risk `legal`, escalated to the legal team.

> "It picked up indemnification and liability language, tagged it legal risk, and
> routed it rather than answering. The copilot is explicitly not deciding a
> liability cap on the COO's behalf."

**The outage (Priya Nair, 07:22)** — risk `ops`, a drafted reply held for approval.

> "This is the interesting one. It decided a reply is warranted, drafted it, and
> then stopped. Anything that reaches the outside world waits for a human."

If the draft carries a **model-drafted** chip, that is the point to make the
distinction that matters:

> "The model wrote those words. It did not decide to send them. Priority, risk
> and the choice between reply, escalate, defer and file are computed by
> deterministic code that runs identically with the model switched off — which is
> why we can test it and why it costs nothing when the model is unavailable."

**The wire-transfer invoice (Daniel Mensah, 07:31)** — finance risk.

> "A supplier changed their bank details mid-invoice and the account name doesn't
> match. That's the standard shape of supplier fraud. It's flagged, drafted, and
> waiting."

**The legitimate invoice (Atlas Logistics, 06:12)** — finance risk, no action.

> "Same vocabulary, opposite outcome. An established supplier, unchanged banking
> details, a normal payment window — filed, not flagged. A detector that flags
> every invoice hasn't detected anything."

Then scroll the list: six promotional messages classified as spam and filed with
no human involvement, and roughly thirty routine items deferred with a label.

> "Note what *didn't* happen: nothing was sent, and none of the noise reached the
> approval queue."

**If someone asks whether it's just keyword matching**, the mailbox contains
deliberate near-misses. `m-nearmiss-monday` says "Monday", "agenda", "mandate"
and "standard terms" — every one of them contains a legal risk term as a
substring, and it stays unflagged. `m-security-access` says "contractor", which
is *not* a contract, and routes to security rather than legal.

### 6. Approvals — `/app/approvals`

Eleven actions waiting, each showing the reasoning behind it before the buttons.
Approve one.

> "That's the whole control model. Replies and escalations queue here.
> Classifications and deferrals apply themselves, because the worst they can do
> is add a label."

### 7. Activity — `/app/activity`

> "Every security-relevant action is appended to a per-organization audit log —
> sign-ins, mailbox connections, syncs, and every approval decision, with who did
> it and when. This is usually the first thing procurement asks for."

### 8. Settings — `/app/settings`

Plan and seat usage, member management (invite with a one-time temporary
password, change role, remove — seat limits and the last-owner guard enforced),
license-key activation, change password, connected mailboxes, and the owner's
data section: a one-click JSON export of everything the tenant owns, and
permanent deletion gated on retyping the workspace slug.

> "The plan is enforced, not decorative: an expired trial blocks syncing and
> approvals with a clear 402 — sign-in and settings stay open, because an admin
> needs those exactly when the plan has lapsed."

---

## What is real, and what is not

Be direct about this. It lands better than hedging.

**Real:**

- The triage decisions. Priority, risk, deadline, business value, and the choice
  between reply / escalate / defer / file are computed at request time by
  `app/copilot/policy.py` from signals inferred by `app/copilot/enrich.py`. This
  is the same code that runs against a real Gmail account. Edit a subject line in
  `data/demo/inbox.json` and the routing changes.
- Multi-tenancy, RBAC, and the audit log. Every row is scoped to an organization.
- The approval gate, and the fact that approving dispatches to the provider's
  write surface.
- OAuth for Gmail and Microsoft Graph, including token refresh and encryption at
  rest. It just needs credentials configured.
- The benchmark numbers on the landing page.
- The drafted prose, when the **model-drafted** chip is showing. Those words were
  generated by the configured model against the real message, through
  `app/llm/drafter.py`, and cached to `data/demo/drafts.json` at seed time. They
  are replayed rather than regenerated, but they are not written by hand.

**Simulated:**

- The mailbox contents. Fifty fixture messages, not a live inbox.
- Approving a reply in the demo records the send rather than transmitting it.
- Without a generated cache, the drafted wording falls back to authored fixture
  prose (and to one generic policy sentence beyond that). The seeder prints which
  of the three you are running with.

If asked **"so is the AI real?"** — split the question, because the honest answer
has two halves:

- **The decisions are deterministic, deliberately.** Priority, risk, deadline and
  the choice between reply / escalate / defer / file are computed by
  `app/copilot/policy.py` from signals inferred in `app/copilot/enrich.py`. That
  is reproducible, testable, free to run, and was *selected* by the benchmark in
  this repo rather than guessed. It also means a model outage degrades the prose
  and nothing else.
- **The prose is model-written.** `app/llm/drafter.py` runs a real provider over
  the real message and returns the reply or the escalation handover note. It is
  scoped so it can never decide anything: it is handed a decision already made
  and asked only for words.

If asked **"why not let the model decide too?"** — because the approval queue is
the product. A model that both decides and writes gives a reviewer nothing stable
to check against. Splitting them means the routing is covered by tests and the
wording is where the model adds value.

If asked **"what stops prompt injection?"** — an inbound message is scanned before
it reaches the model, and a message that tries to rewrite the instructions is
never sent to a provider at all; it falls back to fixture prose and still reaches
a human. The generated draft is scanned again on the way out. See
`tests/test_llm_drafter.py`.

---

## Likely questions

**"What happens if the model is wrong?"**
Nothing leaves the building without a human. The approval queue is the product,
not a feature of it. Classifications and deferrals auto-apply because their worst
case is a mislabelled message.

**"How do you keep one customer's mail away from another's?"**
Every customer-owned row carries an `org_id` and all access goes through
tenant-scoped repositories in `app/saas/repository.py`. There is a multi-tenancy
test suite (`tests/test_multitenant.py`).

**"Where do the OAuth tokens live?"**
Encrypted at rest with authenticated encryption, decrypted in exactly one
auditable module (`app/saas/provider_factory.py`). They are never returned by any
API — the serializers omit them.

**"Can we self-host?"**
Yes. One container, SQLite by default, `DATABASE_URL` for Postgres. There is a
Helm chart under `helm/` and a Render blueprint.

**"How is this tested?"**
768 tests. The demo path you just walked is covered end to end in
`tests/test_web_pages.py`, including the session gate, CSRF, and that approving
actually transitions the action and records an audit entry.

---

## If something goes wrong

- **Inbox is empty** — the mailbox is connected but not synced. Click **Sync
  mailbox**, or re-run `make demo`.
- **Login rejected** — the demo account doesn't exist yet. Run `make demo`.
- **A form returns 403** — the CSRF token expired (they last 8 hours). Reload.
- **Port in use** — `uvicorn app.main:app --port 8001`.
- **Total reset** — `python scripts/seed_demo.py --fresh`.
