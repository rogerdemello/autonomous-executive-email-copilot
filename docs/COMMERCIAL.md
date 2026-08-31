# Commercial / SaaS Guide

This document describes the **commercial foundation** that turns the copilot from
a single-tenant product demo into a multi-tenant SaaS: accounts,
organizations (tenants), role-based access, and license-key entitlements.

It is **additive** — the benchmark, deterministic scoring contract, and existing
unversioned API are untouched. All SaaS code lives under [`app/saas/`](../app/saas).

---

## 1. Concepts

| Concept | What it is |
|---|---|
| **Organization** | A customer tenant. All customer-owned data is scoped to an org. |
| **User** | A person who signs in. Belongs to exactly one organization. |
| **Role** | `owner` > `admin` > `member`. Gates what a user may do (RBAC). |
| **License** | A sales-issued, signed entitlement grant (plan, seats, features, expiry). |
| **Entitlement** | The live view of what an org may do = signed license terms ∩ DB status. |
| **Audit log** | Append-only record of security-relevant actions per org. |

### Roles

- **owner** — full control incl. billing (activate licenses) and members.
- **admin** — manage members (invite/remove/role) but not billing.
- **member** — use the product; no management.

A member can never be granted a role above the granter's own (no privilege
escalation), and an org must always keep at least one owner.

---

## 2. Onboarding flows

### Self-serve trial (default)
`POST /auth/signup` provisions an **organization + owner user + 14-day trial
license** in one call and returns a session token. Disable this for a pure
sales-led motion with `SIGNUP_ENABLED=false`.

```bash
curl -sX POST localhost:8000/auth/signup -H 'Content-Type: application/json' -d '{
  "email":"ceo@acme.com","password":"a-strong-password","full_name":"A. Vance","org_name":"Acme Inc"
}'
# -> { access_token, user{role:owner}, organization }
```

### Login
```bash
curl -sX POST localhost:8000/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"ceo@acme.com","password":"a-strong-password"}'
```

### Password reset
`POST /auth/forgot-password {email}` always returns 200 (never reveals whether an
email exists) and, when it does, emails a signed, short-lived reset link.
`POST /auth/reset-password {token, new_password}` verifies the link and sets the
new password. Email is **pluggable**: the default `EMAIL_PROVIDER=console` just
logs the message (zero-config for dev/tests); set `EMAIL_PROVIDER=smtp` + the
`SMTP_*` settings to deliver for real. Member invites email the new member their
temporary password the same way.

Use the returned token as `Authorization: Bearer <token>` on all authenticated
routes (`/auth/me`, `/org/*`, `/billing/*`).

---

## 3. Access keys (billing)

Acquisition is self-serve; **payment** is not. There is no card capture: a
visitor signs up, connects a mailbox, and gets 14 days with everything on.
Keeping access after that is arranged by conversation, and granted as a signed
key. The motion is:

1. A trial ends, or a prospect submits a lead: the `/contact-sales` form
   (CSRF + honeypot +
   per-IP throttle) or `POST /billing/contact-sales` (public JSON). Leads are
   persisted (`GET /operator/leads` reads them back), and optionally posted to
   `SALES_WEBHOOK_URL`.
2. After a contract is signed, an operator mints a **signed license key** bound
   to the customer's org id — against a deployed instance, via the operator
   API (see [PROVISIONING_RUNBOOK.md](PROVISIONING_RUNBOOK.md)); locally:

   ```bash
   python scripts/issue_license.py --org <org_id> --plan business --valid-days 365 --persist
   ```

   The key is signed with `AUTH_SECRET_KEY` and encodes plan, seats, features,
   and expiry, so it verifies **offline**. `--persist` also writes the license
   row, enabling **revocation** and server-side seat enforcement.
3. The customer's **owner** activates it:

   ```bash
   curl -sX POST localhost:8000/billing/activate-license \
     -H "Authorization: Bearer <owner_token>" -H 'Content-Type: application/json' \
     -d '{"license_key":"<key>"}'
   ```
4. Entitlement is now live: `GET /billing/entitlement`.

### Grants

Grants are defined once in [`app/saas/licensing.py`](../app/saas/licensing.py)
(`PLANS`) and drive entitlement checks. They are an **internal vocabulary for
what a key carries** — nothing renders them to a visitor or to a customer.
There is no price anywhere in this codebase and no page that lists tiers;
`/pricing` 301s to the landing page and Settings shows "Trial · N days
remaining" or "Full access", never a tier name.

| Grant | Seats (default) | Notable features |
|---|---|---|
| Trial | 3 | Approvals, analytics, audit log, SSO — 14-day term |
| Team | 10 | Approvals, analytics |
| Business | 50 | + Audit log, SSO |
| Enterprise | 1000 | + Priority support, custom models |

Only `audit_log` and `sso` are actually **enforced** anywhere
(`web/routes.py:activity` and the SSO login route). The other four flags are
carried by keys and checked by nothing — they used to render as chips in
Settings, which made them a decorative claim about what a customer had bought.
Do not surface a flag until it gates something.

Seats and features can be overridden per key at mint time
(`--seats`, or the `mint_license(features=...)` argument).

---

## 4. API surface

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/auth/signup` | public | Create org + owner + trial |
| POST | `/auth/login` | public | Get a session token |
| POST | `/auth/forgot-password` | public | Email a password-reset link |
| POST | `/auth/reset-password` | public | Set a new password from a reset token |
| GET | `/auth/me` | user | Current user + org |
| POST | `/auth/change-password` | user | Rotate own password |
| GET | `/org` | user | Org + entitlement + member count |
| GET | `/org/members` | user | List org members |
| POST | `/org/members` | admin+ | Invite a member (consumes a seat) |
| PATCH | `/org/members/{id}/role` | admin+ | Change a member's role |
| DELETE | `/org/members/{id}` | admin+ | Remove a member |
| GET | `/org/audit-log` | admin+ | Recent security events |
| GET | `/org/export` | owner | Export all org data (secret-free JSON) |
| DELETE | `/org` | owner | Permanently delete the org + all data (GDPR) |
| GET | `/billing/entitlement` | user | Live entitlement |
| POST | `/billing/activate-license` | owner | Activate a license key |
| POST | `/billing/contact-sales` | public | Capture a sales lead |
| GET | `/mailbox/providers` | user | Providers + whether each is configured |
| GET | `/mailbox/connections` | user | Connected mailboxes (no token material) |
| POST | `/mailbox/connect/{provider}` | admin+ | Begin OAuth; returns consent URL |
| GET | `/mailbox/oauth/callback` | public | OAuth redirect target (identity in signed state) |
| DELETE | `/mailbox/connections/{id}` | admin+ | Disconnect a mailbox |
| GET | `/`, `/contact-sales` | public | Landing page + lead form |
| GET | `/privacy`, `/terms` | public | Legal pages (the Gmail verification gate) |
| * | `/operator/*` | operator token | Provision orgs, mint/revoke licenses, read leads, reseed the demo |

The server-rendered UI ([`app/web`](../app/web)) exposes this as real pages
rather than API calls: `/signup` and `/login` for onboarding, `/app/settings` for
the workspace, members and roles, and access, and `/app/connect` for
mailbox connect and disconnect.

Both surfaces share one identity model. The pages carry the *same* session token
the API accepts as `Authorization: Bearer`, in an HttpOnly `SameSite=Lax` cookie —
so role checks and seat limits are enforced once, in `app/saas/deps.py`, not
duplicated per surface. Because cookies are sent automatically, every mutating
form additionally carries a signed CSRF token bound to that session. The operator
`API_AUTH_TOKEN` is separate and never conflicts with a user session.

## Connecting real mailboxes (Gmail / Microsoft 365)

`app/saas/oauth.py` implements the OAuth 2.0 authorization-code flow. A provider
is available only when its client id **and** secret are set
(`GOOGLE_OAUTH_CLIENT_ID/SECRET`, `MICROSOFT_OAUTH_CLIENT_ID/SECRET`). Flow:
`POST /mailbox/connect/{provider}` → consent screen → `GET /mailbox/oauth/callback`
→ code exchanged for tokens → an encrypted, tenant-scoped `MailboxConnection` row.
OAuth tokens are encrypted at rest with a key derived from `AUTH_SECRET_KEY`
(`app/saas/crypto.py`); the API never serializes token material.

---

## 5. Data lifecycle (GDPR)

Owners can **export** (`GET /org/export`) a complete, secret-free JSON bundle of
everything the org owns (org, users, licenses, mailboxes, processed messages,
proposed actions, audit log, leads — no password hashes or OAuth tokens), and
**erase** (`DELETE /org`, body `{"confirm": "<org-slug>"}`) the organization and
every tenant-scoped row in one transaction. Both live in
[`app/saas/data_lifecycle.py`](../app/saas/data_lifecycle.py) — the single place
that enumerates every tenant table, so a new product table gets covered by adding
it there. The dashboard's Account tab surfaces both under a "Data & danger zone"
card (owner only, delete gated behind typing the slug).

## 6. Security model

- **Passwords**: PBKDF2-HMAC-SHA256 with a per-password salt (stdlib, no compiled
  dep). Self-describing hashes support transparent iteration upgrades.
- **Session tokens**: compact JWS (HS256) signed with `AUTH_SECRET_KEY`. Stateless;
  the current user is re-read from the DB on each request so role changes and
  disablement take effect immediately.
- **Tenant isolation**: every org read/write goes through
  [`app/saas/repository.py`](../app/saas/repository.py) and is filtered by
  `org_id`. The one deliberately un-scoped read is login-by-email (org unknown
  until resolved). Cross-tenant access returns 404, never another org's data.
- **License / operator token separation**: `AUTH_SECRET_KEY` signs user tokens
  and licenses; the legacy `API_AUTH_TOKEN` is a separate operator-level gate for
  the benchmark API. Public SaaS auth endpoints bypass that gate by design.

> **Production checklist:** set a strong `AUTH_SECRET_KEY`, set `CORS_ORIGINS` to
> your domains, put the app behind TLS, and set `RATE_LIMIT_PER_MINUTE`. See
> [SECURITY.md](../SECURITY.md) and [docs/RUNBOOK.md](RUNBOOK.md).

---

## 7. What's next (beyond the foundation)

Delivered so far: accounts, orgs, RBAC, sales-led licensing, marketing pages,
**connected mailboxes (Gmail/M365 OAuth)**, a dashboard Account UI, and the
**real-inbox processing pipeline** below.

### Real-inbox processing (the copilot working a connected mailbox)

The gold-free runtime lives in [`app/copilot/`](../app/copilot); the SaaS glue
(tenant persistence, provider factory, sync service, routes) is in `app/saas`.
Flow: `POST /inbox/sync` → fetch via a `MailProvider` (Gmail / Microsoft Graph,
or an in-memory fake for dev/tests) → enrich each message with inferred
signals → run `BaselinePolicy` → persist a tenant-scoped `ProcessedMessage` +
`ProposedAction` per email. External actions (reply/escalate) are **held for
approval**; low-risk ones (classify/defer) auto-apply as labels. `POST
/inbox/actions/{id}/approve|reject` dispatches to the provider's write surface
and records the human `outcome` — the quality signal that replaces the sim's
grader. OAuth access tokens refresh automatically (encrypted at rest); the
in-memory `FakeProvider` means the whole pipeline runs and tests with no
external accounts.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/inbox/sync` | admin+ | Fetch + process a connected mailbox |
| GET | `/inbox/messages` | user | Processed messages (tenant-scoped) |
| GET | `/inbox/actions?status=` | user | Proposed/decided/executed actions |
| POST | `/inbox/actions/{id}/approve` | admin+ | Approve → execute via provider |
| POST | `/inbox/actions/{id}/reject` | admin+ | Reject (records the outcome) |

### Natural follow-ons

- **Background worker/queue** so sync runs on a schedule, not just on demand
  (`InboxSyncService.sync` is already synchronous + idempotent, ready to enqueue).
- **Email delivery** for invites and password reset (invite currently sets a temp
  password the member changes on first sign-in).
- **SSO (SAML/OIDC)** wiring for the `sso` feature flag; Stripe if a self-serve
  tier is later added.
