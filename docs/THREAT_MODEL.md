# Threat Model

A concise, honest threat model for the commercial Executive Email Copilot. It
follows a lightweight STRIDE-per-boundary approach and states what is mitigated,
what is the operator's responsibility, and what is explicitly out of scope today.

## Assets

| Asset | Sensitivity | Where it lives |
|---|---|---|
| OAuth access/refresh tokens (mailbox access) | **Critical** | `saas_mailbox_connections`, encrypted (Fernet) |
| User password hashes | High | `saas_users` (PBKDF2) |
| `AUTH_SECRET_KEY` (signs tokens/licenses, derives vault key) | **Critical** | environment / secret manager |
| Processed message content + proposed actions | High | `saas_processed_messages`, `saas_proposed_actions` |
| Session tokens / license keys | High | client-held, signed |
| Audit log | Medium | `saas_audit_log` |

## Trust boundaries

1. **Untrusted client → API.** Anonymous internet to FastAPI.
2. **Tenant → tenant.** One organization must never read/act on another's data.
3. **App → mail provider.** Delegated OAuth to Gmail / Microsoft Graph.
4. **App → database / secret store.** Persistence and key material.

## STRIDE summary

| Threat | Vector | Mitigation | Residual / operator duty |
|---|---|---|---|
| **Spoofing** | Forged session/login | HS256-signed tokens; server-side user re-read; uniform login errors (no user enumeration) | Set strong `AUTH_SECRET_KEY`; enable TLS |
| **Tampering** | Modified token or ciphertext | Signature verification; Fernet/HMAC authenticated encryption rejects tampered blobs | — |
| **Repudiation** | "I didn't do that" | Per-org append-only audit log of auth, member, license, mailbox, and inbox actions | Ship logs to a WORM/SIEM sink |
| **Information disclosure** | Cross-tenant read; token leakage | `org_id`-scoped repositories (404 on cross-tenant); tokens encrypted at rest and never serialized | Restrict `CORS_ORIGINS`; secret manager for keys |
| **Denial of service** | Request floods; unbounded queries | Opt-in per-IP rate limiting; bounded pagination on list endpoints | Front with a WAF/CDN; set `RATE_LIMIT_PER_MINUTE` |
| **Elevation of privilege** | Member acting as admin/owner | RBAC (owner>admin>member); no assigning a role above your own; last-owner protection; billing/mailbox mutations require admin/owner | Review member roles periodically |

## Known limitations / follow-ups (stated honestly)

- **OIDC `id_token` is decoded, not signature-verified** — used only to *label* a
  connection with its account email (never as an auth credential). A spoofed
  `email` claim could mislabel a mailbox's account/role inference. Verifying the
  provider JWKS signature is a planned hardening.
- **`AUTH_SECRET_KEY` is a single high-value secret** (tokens + licenses + vault
  key). Splitting into purpose-specific keys and supporting KMS-backed keys is a
  follow-up. A missing key logs a warning but does not hard-fail — production
  deployments should treat a dev-secret warning as a release blocker.
- **SSO (SAML/OIDC) is not yet implemented**; the `sso` plan feature is
  entitlement-gated but the login integration is a roadmap item.
- **Read (GET) endpoints on the research/benchmark/telemetry surface are open by design**
  even when `API_AUTH_TOKEN` is set (only mutations are gated there). The
  customer SaaS surface (`/auth`, `/org`, `/billing`, `/mailbox`, `/inbox`)
  enforces its own per-user session auth on both reads and writes.
- **Background token refresh** happens lazily on a 401 during sync; a proactive
  refresh scheduler is a follow-up.

## Verification

Security-relevant behavior is covered by automated tests (tenant isolation,
RBAC, token tamper rejection, encrypted-at-rest tokens, approval gating) and by
CI scanning (bandit, pip-audit, CodeQL, gitleaks, Trivy). See
[SECURITY.md](../SECURITY.md).
