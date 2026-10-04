# Demo walkthrough

A script for showing this to someone in about eight minutes, and an honest
account of what is real and what is simulated — so you are never caught out by
the follow-up question.

## Before you start

**Hosted.** Open the live instance and press **Try the live demo** on the landing
page. If nobody has used it recently the host may need up to a minute to wake;
open it a couple of minutes before you present.

**Locally:**

```bash
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

Open `http://localhost:8000` and press **Try the live demo**. There is nothing to
seed: the button builds the visitor a private sandbox workspace — organization,
owner, mailbox attached and triaged — in about a second, and signs them in. It
works on an empty database, and every visitor starts from the same untouched
inbox, so approving everything in one rehearsal does not empty the next.

Optional: `make demo` (or `python scripts/seed_demo.py`) also seeds a *shared*
demo account with a prefilled sign-in, which some sales calls prefer. It is
idempotent — run it again between rehearsals to reset it — and
`python scripts/seed_demo.py --fresh` deletes the organization and rebuilds it.

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
Triaged 51 messages: 82 applied automatically, 12 held for approval
  Drafts: 11 model-written
```

If it says `authored fixture prose` or `the policy's generic sentence` instead,
the cache is empty — the demo still works, it just falls back to the written
fixtures. Check this line before you present.

The shared account, if you seeded it, is `alex.chen@northwind.example` /
`demo1234`, and the login page prefills it. The one-click demo needs neither.

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

### 2. The funnel — `/signup` and `/contact-sales`

There is no pricing page. `/pricing` 301s to the homepage, so do not open it.

> "Self-serve. Sign up, connect a mailbox, and you have fourteen days with
> every feature switched on — no card, nothing to cancel. When the trial ends,
> syncing and approvals pause and nothing is deleted. What it costs after that
> is a conversation, which is why there is no price on the site."

### 3. Open the demo — **Try the live demo**

Press the button under the headline. One click, no form: you land in `/app/inbox`
with a two-minute tour across the top, and a **Demo sandbox** strip on every page
saying what this is.

> "That just built me a private workspace. Nobody else is in it, nothing I do
> here is sent anywhere, and it is deleted when I sign out."

If someone asks about sign-in: `/login` is the ordinary form, and it also offers
the demo button for anyone without an account. If someone asks about SSO: the **Sign in with SSO** button appears when `OIDC_ISSUER`,
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

(Inside a demo sandbox the demo mailbox is already attached, which is why the
inbox was full the moment you arrived. To show this step happening, sign up for a
throwaway workspace at `/signup` and connect it here.)

### 5. The inbox — `/app/inbox`

Fifty-one messages in a plausible COO's morning. Start with the summary bar at
the top, because it is the whole argument in one line:

> **51 triaged · 82 applied automatically · 12 need you**
>
> "Fifty-one messages arrived. Twelve need the COO. That ratio is the product —
> everything else is how it earns the right to claim it."

Those four numbers describe the workspace, not the current view: put `spam` in
the classification filter and the list drops to eight while the summary stays
put. That is deliberate — a filter that rewrites your headline metrics is a
filter you cannot trust.

The list below is ranked the way the copilot ranked it, with priority and risk
chips. Click through four, in this order:

**The contract (Rachel Okafor, 07:12)** — risk `legal`, escalated to the legal team.

> "It picked up indemnification and liability language, tagged it legal risk, and
> routed it rather than answering. The copilot is explicitly not deciding a
> liability cap on the COO's behalf."

**The datacentre incident (Marcus Reid, 09:15)** — risk `ops`, two messages in one
thread, a drafted reply held for approval.

> "This is the interesting one. It decided a reply is warranted, drafted it, and
> then stopped. Anything that reaches the outside world waits for a human."

Note the **2 in thread** chip in the list and the Thread panel under the message:
this is the second message in a live incident, and the copilot's reasoning names
the earlier decision it depends on. Click the first message in the thread panel
to walk backwards through it.

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

Then set the classification filter to `spam`: eight promotional messages
classified and filed with no human involvement. Clear it, and roughly thirty
routine items sit deferred with a label.

> "Note what *didn't* happen: nothing was sent, and none of the noise reached the
> approval queue."

**If someone asks whether it's just keyword matching**, the mailbox contains
deliberate near-misses. `m-nearmiss-monday` says "Monday", "agenda", "mandate"
and "standard terms" — every one of them contains a legal risk term as a
substring, and it stays unflagged. `m-security-access` says "contractor", which
is *not* a contract, and routes to security rather than legal.

### 6. Approvals — `/app/approvals`

Twelve actions waiting, each showing the reasoning behind it before the buttons.
The draft is a textarea — change a sentence, then approve.

> "That's the whole control model. Replies and escalations queue here.
> Classifications and deferrals apply themselves, because the worst they can do
> is add a label."

> "Every held draft also carries a verification verdict — a second pass checked
> the prose against the source message before it queued. Ten say 'verified'. Two
> say 'check flagged'. In one, the model wrote 'by 25 September' for a message
> whose deadline is 30 September, and the verifier caught it: it shows the
> sentence, the source line it failed against, and a button that removes it.
> That's the system catching its own model inventing a fact, in front of you."

The tour's third step links straight to the first flagged draft.

> "And notice the draft is editable. What you send is what you wrote — and the
> copilot keeps the pair. Your edits become voice examples for future drafts,
> and a proposal shape you keep rejecting stops being proposed: after three
> straight rejections it gets filed as deferred, with the reason written on the
> action. Once there are decisions to learn from, this page grows a 'what the
> copilot has learned' panel that names them."

### 7. Waiting on — `/app/waiting`

> "This is the thing none of the alternatives do. Every review of every AI email
> tool names the same gap: nothing tracks follow-ups. The copilot reads the
> promises out of your mail as they are made, in both directions — what someone
> owes you, and what you committed to in a reply you approved — and shows you the
> sentence it came from so you can check it in one glance instead of re-reading
> the thread."

Point at a row with a resolved date and hover the date chip: it names the words
the sender actually used. Then point at one with **no date**.

> "It didn't guess. Nobody stated a deadline there, so it isn't going to invent
> one and then nag you about it — that's the same failure the draft verifier
> exists to catch, one surface over."

If asked how it avoids becoming noise: spam is excluded using the copilot's own
classification, and every row can be dismissed as **Not a commitment** — kept
separate from **Mark done** on purpose, because "this was never real" is
different feedback from "this is finished".

### 8. Activity — `/app/activity`

> "Every security-relevant action is appended to a per-organization audit log —
> sign-ins, mailbox connections, syncs, and every approval decision, with who did
> it and when. This is usually the first thing procurement asks for."

### 9. Settings — `/app/settings`

Access ("Trial · N days remaining", and the way to keep it), member management
(invite with a one-time temporary password, change role, remove — seat limits
and the last-owner guard enforced), access-key activation, change password,
connected mailboxes, and the owner's data section: a one-click JSON export of
everything the tenant owns, and permanent deletion gated on retyping the
workspace slug.

> "Access is enforced, not decorative: an expired trial blocks syncing and
> approvals with a clear 402 — sign-in, settings, export and delete stay open,
> because those are exactly what you need when access has lapsed."

In a demo sandbox most of this page is deliberately read-only — password, access
key, escalation contacts, members, export and delete are hidden, because a
sandbox belongs to an anonymous stranger and the server refuses them anyway. To
walk through them, sign up for a real throwaway workspace.

---

## What is real, and what is not

Be direct about this. It lands better than hedging.

**Real:**

- Search, the classification and priority filters, paging, and thread grouping.
  `thread_id` has been stored since the table existed; the inbox now reads it.

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

**The sandbox itself is real too** — and worth explaining if asked why the demo
is not a shared login. Each click builds a genuine workspace through the same code
a signup uses (organization, owner, trial license), attaches the demo mailbox and
triages it with the production pipeline. Because it is private there is nothing to
reset, and because it is built on demand it works on an empty database. It is
safe to hang off an unauthenticated button because of what bounds it
(`app/saas/sandbox.py`, pinned by `tests/test_demo_sandbox.py`):

- **Bounded.** A per-address rate limit (8 per ten minutes) and a hard cap on live
  sandboxes that refuses new ones rather than evicting someone mid-demo.
- **Self-cleaning.** Deleted when the session that opened it would have expired,
  when the visitor signs out, or immediately if the build fails — by the same
  hard-delete a customer's erasure uses.
- **Unable to spend.** A sandbox never drafts with a live model, even on a
  deployment where drafting is on, so a stranger cannot run up the model bill.
- **Unable to reach out.** No real mailbox can be connected, no member invited, no
  email sent (its owner's address is on `.invalid`, which never delivers), no
  password or license changed. The background worker never sweeps it.

The honest limit: the per-address limit trusts the proxy's `X-Forwarded-For`,
which a client can prepend to, so the *cap* is what bounds a determined abuser.

**Simulated:**

- The mailbox contents. Fifty-one fixture messages, not a live inbox.
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
Well over a thousand tests; the README has the current count and the command that
reproduces it. The demo path you just walked is covered end to end in
`tests/test_web_pages.py` and `tests/test_demo_sandbox.py` — including the session
gate, CSRF, isolation between visitors, and that approving actually transitions
the action and records an audit entry — and a real browser drives the same path
at phone width in `tests/test_web_reflow.py`.

---

## If something goes wrong

- **The hosted page shows "service waking up"** — the free host slept. Wait a
  minute; the next visitor will not see it.
- **Inbox is empty** — you are in a sandbox you have already emptied, or the
  mailbox is not synced. Click **Sync mailbox**, or sign out and press **Try the
  live demo** again for a fresh one.
- **The demo button says it is busy, or that you opened it several times** — the
  sandbox is rate-limited and capped. Wait a few minutes.
- **You were sent to the sign-in page mid-demo** — the session or the host's
  database was reset. Press **Open the live demo** there.
- **A form returns 403** — the CSRF token expired (they last 8 hours). Reload.
- **Port in use** — `uvicorn app.main:app --port 8001`.
- **Shared account: login rejected or stale** — `make demo`, or
  `python scripts/seed_demo.py --fresh` for a total reset.
