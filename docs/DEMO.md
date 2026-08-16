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

Fourteen messages in a plausible COO's morning. The list is ranked the way the
copilot ranked it, with priority and risk chips.

Click through three, in this order:

**The contract (Rachel Okafor, 07:12)** — risk `legal`, escalated to the legal team.

> "It picked up indemnification and liability language, tagged it legal risk, and
> routed it rather than answering. The copilot is explicitly not deciding a
> liability cap on the COO's behalf."

**The outage (Priya Nair, 07:22)** — a drafted reply, held for approval.

> "This is the interesting one. It decided a reply is warranted, drafted it, and
> then stopped. Anything that reaches the outside world waits for a human."

**The wire-transfer invoice (Daniel Mensah, 07:31)** — finance risk.

> "A supplier changed their bank details mid-invoice and the account name doesn't
> match. That's the standard shape of supplier fraud. It's flagged, drafted, and
> waiting."

Then scroll the list to the bottom: two promotional messages, classified as spam
and filed with no human involvement.

> "Note what *didn't* happen: nothing was sent, and the noise never reached the
> approval queue."

### 6. Approvals — `/app/approvals`

Six actions waiting. Approve one.

> "That's the whole control model. Replies and escalations queue here.
> Classifications and deferrals apply themselves, because the worst they can do
> is add a label."

### 7. Activity — `/app/activity`

> "Every security-relevant action is appended to a per-organization audit log —
> sign-ins, mailbox connections, syncs, and every approval decision, with who did
> it and when. This is usually the first thing procurement asks for."

### 8. Settings — `/app/settings`

Plan and seat usage, members with roles, connected mailboxes.

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

**Simulated:**

- The mailbox contents. Fourteen fixture messages, not a live inbox.
- The *wording* of the drafted replies. The policy decides whether to reply; it is
  a router, not a writer, and emits one generic sentence for every reply. The demo
  supplies authored prose per message. With an LLM provider configured, the
  model-backed path in `app/llm` generates drafts instead.
- Approving a reply in the demo records the send rather than transmitting it.

If asked "so is the AI real?" — the honest answer is that the *decision* layer is
deterministic by design, and that this is a feature: it is reproducible, testable,
costs nothing to run, and was selected by a benchmark. The LLM is an optional
upgrade path, not the load-bearing part.

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
682 tests. The demo path you just walked is covered end to end in
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
