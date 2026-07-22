# Security Policy

Executive Email Copilot processes sensitive customer email and holds delegated
OAuth access to customer mailboxes, so security is a first-class concern. This
policy covers vulnerability reporting, our security controls, and the trust
boundaries operators should understand before deploying.

## Reporting a vulnerability

**Please do not open a public issue for security vulnerabilities.**

Report privately via a GitHub [security advisory](https://docs.github.com/en/code-security/security-advisories)
or the contact in [`.well-known/security.txt`](.well-known/security.txt). Include
a description, reproduction steps, affected versions, and impact. We aim to
acknowledge within **2 business days** and to provide a remediation timeline
after triage. We support coordinated disclosure and will credit reporters who
request it.

## Product security controls

### Authentication & sessions
- Passwords are hashed with **PBKDF2-HMAC-SHA256** (per-password salt, high
  iteration count, transparent upgrade path). Plaintext passwords are never
  stored or logged.
- Sessions use **signed (HS256) tokens** with short TTLs; the current user is
  re-read from the database on every request, so role changes and disablement
  take effect immediately.
- Password-reset and invite flows use signed, single-purpose, short-lived tokens
  and never reveal whether an email is registered.

### Multi-tenant isolation
- Every customer record is scoped to an **organization**; all org data access is
  routed through tenant-scoped repositories that filter by `org_id`. Cross-tenant
  access returns 404, never another tenant's data. This is exercised by
  isolation tests in CI.

### Secrets at rest
- OAuth access/refresh tokens (delegated mailbox credentials) are **encrypted at
  rest** using audited **Fernet (AES-128-CBC + HMAC-SHA256)** when the
  `cryptography` package is present, keyed from `AUTH_SECRET_KEY`. A stdlib
  authenticated fallback exists for minimal deployments. The API never serializes
  token material.
- `AUTH_SECRET_KEY` signs session tokens and license keys and derives the token
  vault key. It **must** be set to a long random value in production; a missing
  key falls back to a clearly-marked development secret and logs a warning at
  startup. **Rotating it invalidates all sessions, licenses, and stored tokens.**

### Transport, access & abuse controls (operator-configured)
- `API_AUTH_TOKEN` — gate the operator/benchmark API.
- `CORS_ORIGINS` — restrict browser origins (set to your domains).
- `RATE_LIMIT_PER_MINUTE` — per-client request cap.
- `REQUIRE_APPROVAL` — hold reply/escalate actions for human approval.
- Identifier inputs are validated; pagination is bounded; unhandled errors
  return a generic JSON 500 without leaking stack traces.
- Every action on a real mailbox is **audit-logged** per organization, and
  external actions (reply/escalate) default to **held-for-approval**.

### Data lifecycle (GDPR)
- Organization owners can **export** a complete, secret-free copy of their data
  and **permanently delete** the organization and every tenant-scoped record
  (right to erasure), gated behind an explicit confirmation.

## Supply-chain & code security

- CI runs **ruff**, **mypy**, **bandit** (SAST), and **pip-audit** (dependency
  CVEs) on every change; the frontend runs eslint/prettier/type-check/build.
- A dedicated [Security Scan workflow](.github/workflows/security-scan.yml) runs
  **CodeQL** (Python + JS/TS), **gitleaks** (secret scanning over full history),
  and **Trivy** (container image CVE + misconfiguration scan); findings surface
  in the repo Security tab.
- Runtime dependencies are pinned in both `pyproject.toml` and `requirements.txt`.

## Production hardening checklist

Before exposing the product to untrusted networks:

- [ ] Set a strong random `AUTH_SECRET_KEY` (and store it in a secret manager).
- [ ] Set `CORS_ORIGINS` to your exact domains.
- [ ] Terminate TLS in front of the app; do not serve tokens over plaintext.
- [ ] Set `RATE_LIMIT_PER_MINUTE` and, if applicable, `API_AUTH_TOKEN`.
- [ ] Register least-privilege OAuth apps (Gmail/Graph) and rotate their secrets.
- [ ] Configure a real transactional email provider (`EMAIL_PROVIDER=smtp`).
- [ ] Use a managed Postgres (`DATABASE_URL`) with backups for production data.
- [ ] Review [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) and the runbook.

See also [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md), [docs/COMMERCIAL.md](docs/COMMERCIAL.md),
and [docs/RUNBOOK.md](docs/RUNBOOK.md).
