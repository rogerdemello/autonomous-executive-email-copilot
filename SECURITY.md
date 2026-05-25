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

This project is an evaluation environment and reference product. As of this
writing the API ships **without authentication, CORS restrictions, or rate
limiting** by default — do not expose it directly to untrusted networks. Adding
these controls is tracked in [docs/ROADMAP.md](docs/ROADMAP.md) (Phase 2).
