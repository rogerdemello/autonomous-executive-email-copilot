# Security Policy

Executive Email Copilot processes sensitive customer email and holds delegated
OAuth access to customer mailboxes, so security is a first-class concern. This
policy covers vulnerability reporting, our security controls, and the trust
boundaries operators should understand before deploying.

## Reporting a vulnerability

**Please do not open a public issue for security vulnerabilities.**

Report privately via a GitHub [security advisory](https://docs.github.com/en/code-security/security-advisories)
or the contact published at `/.well-known/security.txt` on any running instance
(generated from the deployment's own configuration). Include
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
- `REQUIRE_APPROVAL` — **simulator only.** It gates `app/llm/agent.py`'s
  benchmark agent, not the product. In the product the approval gate is
  *unconditional*: every `reply` and `escalate` on a real mailbox is held
  by `app/saas/sync_service.py` regardless of configuration, and there is
  no setting that turns it off. This entry used to read as though the gate
  were operator-configurable, which is the opposite of the guarantee.
- Identifier inputs are validated; pagination is bounded; unhandled errors
  return a generic JSON 500 without leaking stack traces.
- Every action on a real mailbox is **audit-logged** per organization, and
  external actions (reply/escalate) default to **held-for-approval**.

### Data lifecycle (GDPR)
- Organization owners can **export** a complete, secret-free copy of their data
  and **permanently delete** the organization and every tenant-scoped record
  (right to erasure), gated behind an explicit confirmation.
- "Every tenant-scoped record" is enforced, not asserted: export and delete walk
  one list (`app/saas/data_lifecycle.py::TENANT_TABLES`), and a test reads the
  live schema and fails the build if any table carrying an `org_id` is missing
  from it. (Two tables once were: erasing a workspace left behind the model-spend
  ledger and the colleague addresses in its escalation contacts, and on Postgres
  the foreign key made the delete itself fail. Both are fixed and pinned.)

### The public demo
- "Try the live demo" builds each visitor a **private, auto-deleted sandbox**
  (`app/saas/sandbox.py`), not a shared account with a published password. There
  is no credential to leak: the owner's password is generated and discarded, and
  its address is on `.invalid`, which can never be delivered.
- It is bounded (per-IP rate limit, a hard cap on live sandboxes that refuses
  rather than evicting a visitor), cleans up after itself (deleted at session
  expiry, on sign-out, or when a build fails), cannot spend the deployment's
  model budget, cannot attach a real mailbox, invite anyone, or change what the
  deployment sends, and is never swept by the background worker. In production it
  must be enabled explicitly (`DEMO_LOGIN_ENABLED=true`).
- One honest limit: the per-IP limit trusts the proxy's `X-Forwarded-For`, which
  a client can prepend to. The cap, not the limit, is what bounds the damage.

## Supply-chain & code security

- CI runs **ruff** (lint and format), **mypy**, **bandit** (SAST) and
  **pip-audit** (dependency CVEs) on every change, plus the test suite with a
  coverage gate, the same suite's database-touching half against a real
  **Postgres**, a **Helm** lint/render job that must refuse unsafe
  configurations, and a Docker build with a smoke test. There is no frontend
  build: the UI is server-rendered Jinja with no Node toolchain.
- A dedicated [Security Scan workflow](.github/workflows/security-scan.yml) runs
  **CodeQL** (Python, the two small scripts under `app/web/static`, and the
  workflow files), **gitleaks** (secret scanning over full history), and
  **Trivy** (container image CVE + misconfiguration scan); findings surface in
  the repo Security tab.
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
