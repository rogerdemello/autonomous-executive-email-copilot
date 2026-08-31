# Provisioning Runbook — sales-led operations against a deployed instance

Day-to-day operator procedures for a production deployment (Render or any
host). Everything runs over the **operator API** — the server holds the
signing secret and the database, so nothing secret leaves the box.

## Setup (once)

```bash
export APP=https://exec-email-copilot.onrender.com
export OPERATOR_TOKEN=...   # Render dashboard → the service → Environment → OPERATOR_TOKEN
```

Every call below authenticates with `Authorization: Bearer $OPERATOR_TOKEN`
(or `X-Operator-Token: ...`). With no `OPERATOR_TOKEN` configured on the
server, the `/operator/*` surface does not exist (404).

> ⚠ **Back up `AUTH_SECRET_KEY`** (same Environment page). It signs every
> session, license key, and CSRF token and derives the mailbox-token
> encryption key. If the service is ever recreated, restore the same value —
> a new one invalidates every license key you have issued.

## 1. A lead arrives

Leads land from the public `/contact-sales` form (and are optionally pushed to
`SALES_WEBHOOK_URL` as they arrive). Read the funnel:

```bash
curl -s $APP/operator/leads -H "Authorization: Bearer $OPERATOR_TOKEN"
```

Mark one as worked:

```bash
curl -s -X PATCH $APP/operator/leads/42 \
  -H "Authorization: Bearer $OPERATOR_TOKEN" -H "Content-Type: application/json" \
  -d '{"status": "contacted"}'          # new | contacted | closed
```

## 2. Provision the customer's workspace

Self-serve signup is off in production (`SIGNUP_ENABLED=false`); workspaces
are created here. Omit `password` to get a generated temporary one back —
**it appears exactly once in this response**; hand it to the customer over a
trusted channel and have them change it in Settings.

```bash
curl -s -X POST $APP/operator/orgs \
  -H "Authorization: Bearer $OPERATOR_TOKEN" -H "Content-Type: application/json" \
  -d '{
        "org_name": "Acme Corp",
        "owner_email": "cto@acme.example",
        "owner_name": "Sam Rivera"
      }'
# → { organization: {id: "<org_id>", ...}, owner: {...},
#     temp_password: "…", entitlement: {plan: "trial", ...} }
```

The workspace starts on a 14-day full-featured trial. Note the `org_id` — the
customer list can always recover it:

```bash
curl -s $APP/operator/orgs -H "Authorization: Bearer $OPERATOR_TOKEN"
```

## 3. Contract signed → mint the license

```bash
curl -s -X POST $APP/operator/licenses \
  -H "Authorization: Bearer $OPERATOR_TOKEN" -H "Content-Type: application/json" \
  -d '{"org_id": "<org_id>", "plan": "business", "valid_days": 365}'
# → { license_key: "…", terms: {...} }
```

Optional: `"seats": 250` to override the plan default. Send `license_key` to
the customer; the **owner** pastes it in **Settings → Activate** (or
`POST /billing/activate-license`). Keys are org-bound — a key minted for one
org 403s anywhere else.

## 4. Renewal, downgrade, churn

- **Renewal**: mint a fresh key (step 3) — most recently activated wins.
- **Downgrade / claw back one key**: revoke it by id; the entitlement falls
  back to the next most recent active license:

  ```bash
  curl -s -X POST $APP/operator/licenses/revoke \
    -H "Authorization: Bearer $OPERATOR_TOKEN" -H "Content-Type: application/json" \
    -d '{"org_id": "<org_id>", "key_id": "<key_id from terms>"}'
  ```

- **Full cut-off (non-payment/churn)**: omit `key_id` to revoke *every*
  active license — syncing and approvals stop with a 402 immediately:

  ```bash
  curl -s -X POST $APP/operator/licenses/revoke \
    -H "Authorization: Bearer $OPERATOR_TOKEN" -H "Content-Type: application/json" \
    -d '{"org_id": "<org_id>"}'
  ```

Revoked keys can never be reactivated. Every operator mutation lands in the
audit log.

## 5. Reset the demo between sales calls

The public login page advertises the shared Northwind demo. Anything a
visitor did with it is undone by:

```bash
curl -s -X POST $APP/operator/demo/reseed -H "Authorization: Bearer $OPERATOR_TOKEN"
```

(The demo also reseeds on every service restart: `DEMO_SEED_ON_STARTUP=true`.)

## Known gaps (accepted for launch)

- **Transactional email defaults to `console`** — password-reset and invite
  emails are logged, not sent, until `EMAIL_PROVIDER=smtp` + `SMTP_*` are
  configured. Until then, deliver temp passwords yourself and handle "I'm
  locked out" by re-provisioning or setting SMTP up.
- **Render free Postgres expires after ~30 days.** Move to a paid database
  plan before onboarding a paying customer.
- **`AUTH_SECRET_KEY` rotation is a scorched-earth event** (all sessions,
  licenses, and stored mailbox tokens die). There is no dual-key rotation;
  guard the secret instead.
