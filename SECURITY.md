# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for security vulnerabilities.

Instead, report privately to the maintainers (e.g. via a GitHub
[security advisory](https://docs.github.com/en/code-security/security-advisories)
or a direct message to the repository owner). Include a description, reproduction
steps, and impact assessment. We aim to acknowledge reports within a few business
days.

## Handling secrets

- Never commit API keys, tokens, or credentials. Use `.env` (gitignored) and the
  documented variables in [.env.example](.env.example).
- The application reads provider credentials only from environment variables
  (`OPENAI_API_KEY`, `HF_TOKEN`); they are never logged.

## Scope notes

This project is an evaluation environment and reference product. The API ships
**open by default** so local development, tests, and the OpenEnv validator work
with zero configuration. Security controls are **opt-in** via environment
variables (see [.env.example](.env.example)):

- `API_AUTH_TOKEN` — when set, all mutating routes (POST/PUT/PATCH/DELETE)
  require the token via `Authorization: Bearer <token>` or `X-API-Key`.
- `CORS_ORIGINS` — restrict allowed browser origins (defaults to `*`).
- `RATE_LIMIT_PER_MINUTE` — per-client-IP request cap (defaults to off).

When exposing the API to untrusted networks, set all three. Identifier inputs
are validated and pagination is bounded; unhandled errors return a generic JSON
500 without leaking stack traces.
