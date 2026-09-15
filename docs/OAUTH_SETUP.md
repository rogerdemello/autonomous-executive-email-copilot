# Connecting real mailboxes: Gmail & Microsoft 365 OAuth setup

The product ships with the demo mailbox enabled and **real-provider OAuth
deliberately unconfigured** — selling with the demo requires none of this.
When you're ready to let customers connect real inboxes, register the OAuth
apps below and set the environment variables; the connect buttons light up on
their own (`app/saas/oauth.py` reports a provider "available" only when both
its id and secret are set).

Both providers redirect back to:

```
<APP_PUBLIC_URL>/mailbox/oauth/callback
```

Set `OAUTH_REDIRECT_BASE_URL` (or `APP_PUBLIC_URL`) explicitly in production
so it matches what you register — never rely on request-derived URLs behind a
proxy.

## Microsoft 365 (do this one first — no review process)

1. [Entra admin center](https://entra.microsoft.com) → App registrations →
   **New registration**. Supported account types: *Accounts in any
   organizational directory and personal Microsoft accounts* (matches the
   default `MICROSOFT_OAUTH_TENANT=common`; pin to your tenant id to
   restrict).
2. Redirect URI (Web): `https://<your-host>/mailbox/oauth/callback`.
3. Certificates & secrets → **New client secret** (note it immediately).
4. API permissions → Microsoft Graph → *Delegated*: `Mail.Read`,
   `Mail.ReadWrite`, `Mail.Send`, `offline_access`, `openid`, `email`.
5. Set:
   ```
   MICROSOFT_OAUTH_CLIENT_ID=<application (client) id>
   MICROSOFT_OAUTH_CLIENT_SECRET=<secret value>
   MICROSOFT_OAUTH_TENANT=common
   ```

**Enterprise caveat**: `Mail.ReadWrite`/`Mail.Send` trip admin-consent
prompts in most managed tenants. Give the customer's IT admin the
admin-consent URL
(`https://login.microsoftonline.com/<their-tenant>/adminconsent?client_id=<yours>`)
ahead of the rollout call.

## Gmail (start early — the review takes months)

1. [Google Cloud Console](https://console.cloud.google.com) → new project →
   **APIs & Services → OAuth consent screen**: External, app name, support
   email, and a **published privacy policy URL** (required for verification).
2. **Credentials → Create credentials → OAuth client ID** (Web application),
   authorized redirect URI `https://<your-host>/mailbox/oauth/callback`.
3. Enable the **Gmail API** for the project.
4. Set:
   ```
   GOOGLE_OAUTH_CLIENT_ID=...
   GOOGLE_OAUTH_CLIENT_SECRET=...
   ```

**The scope problem**: the app requests `gmail.modify` and `gmail.compose`.
Google classifies **both** as *restricted* scopes — several third-party blogs
claim `gmail.modify` is merely "sensitive"; they are wrong, and the
[official scope table](https://developers.google.com/gmail/api/auth/scopes) is
the authority. `gmail.readonly` was requested too and has been dropped:
`gmail.modify` already grants read, so it added no capability, and requesting
a scope you do not use is a documented rejection reason.

Until the app passes Google's verification (including a **CASA Tier 2 security
assessment**, re-done every 12 months, roughly $540–$1,000 on the self-serve
lab path and more from some assessors), Gmail connects are limited to
**100 users**, each seeing an "unverified app" warning. There is no scope
arrangement that avoids this — the only escape is not reading the mailbox,
which is the product.

### ⚠ Publishing status decides whether those 100 users are usable at all

This is the detail that decides whether you can onboard a paying client on
Gmail before verification finishes, and it is easy to meet the hard way.

| Consent-screen status | Who can connect | Refresh tokens |
|---|---|---|
| **Testing** | only emails listed as test users (max 100) | **expire after 7 days** |
| **In production**, unverified | anyone, capped at 100 users, all shown the "unverified app" warning | do not expire |
| **In production**, verified | everyone, no warning | do not expire |

A refresh token is what lets the background worker keep syncing after the
first hour. In **Testing**, Google expires it after seven days — so a client
connected in Testing stops syncing every week and has to reconnect by hand.
The product handles this correctly (the connection flips to "needs reconnect",
they get a banner and an email) but a weekly reconnect is not something you can
ask a customer to live with.

**So: move the consent screen to "In production" before onboarding anyone**,
even though it is still unverified. You keep the 100-user cap and the warning
screen, and you lose the seven-day clock. Then run verification and CASA in
parallel.

Plan for:

- the published privacy policy and Limited Use disclosure — **already built**,
  at `/privacy` (`app/web/templates/privacy.html`). Its scope tables are kept
  in sync with `app/saas/oauth.py` by hand and by
  `tests/test_web_pages.py::TestLegalPages`; a mismatch is a rejection reason,
- the verification questionnaire in the Cloud Console,
- the CASA assessment through one of Google's authorized labs (weeks–months).

100 users is likely months of runway for a solo-executive ICP onboarded by
hand — *provided* the consent screen is "In production" rather than "Testing"
(see the table above).

**Microsoft 365 needs no review at all** — register that app first, ship both,
and run CASA in parallel expecting the cap to stop mattering before you reach
it. Note `Mail.ReadWrite`/`Mail.Send` trip admin consent in most managed
tenants; have the admin-consent URL ready for rollout calls.

## Can I connect a client's mailbox today?

| | Outlook / Microsoft 365 | Gmail |
|---|---|---|
| Anyone reviewing **you** first? | No | Yes — verification + CASA, 6–12 weeks |
| Who has to say yes | The client's own IT admin, once, for their tenant | Google, before you leave the 100-user cap |
| Scary warning screen | No | Yes, until verified |
| How many clients | Unlimited | 100, until verified |
| **Usable for a paying client today** | **Yes** | Yes, with a warning screen and a hard cap |

The gate on Microsoft is the *customer's* admin, which is a normal enterprise
sales conversation you can have on the rollout call. The gate on Gmail is
Google, and it is a queue you cannot shorten — which is why it is worth
starting on day one and selling Outlook while it runs.

## Security notes (already handled by the app)

- Tokens at rest are Fernet-encrypted with a key derived from
  `AUTH_SECRET_KEY` (`app/saas/crypto.py`) — which makes secret rotation a
  reconnect-every-mailbox event; guard the secret.
- The OAuth `state` is a signed 15-minute token carrying org/user/provider;
  the callback verifies it before touching anything.
- Connect/disconnect require the admin role, and the shared demo account is
  barred from both.
