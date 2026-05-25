# Contributing

Thanks for your interest in improving the Autonomous Executive Email Copilot.

## Development setup

```bash
python -m venv .venv
# Windows PowerShell:  .\.venv\Scripts\Activate.ps1
# Linux/macOS:         source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # optional; only needed for LLM/hybrid modes
```

Run the API locally:

```bash
uvicorn env.api:app --reload --port 8000
```

Frontend (optional):

```bash
cd dashboard && npm install && npm run dev
```

## Before opening a pull request

1. **Tests must pass:** `python -m pytest -q` (the suite must stay green).
2. **No new deprecation warnings:** `python -m pytest -q -W error::DeprecationWarning`.
3. **Lint/format:** install hooks with `pre-commit install`, or run `pre-commit run --all-files`.
4. **Add tests** for any behavior change — every documented claim should be backed by a test.
5. **Don't commit secrets or runtime artifacts.** `*.db`, generated CSVs, and build output are gitignored; keep them that way.

## Conventions

- Match the style of the surrounding code; the pre-commit config (ruff for Python,
  eslint/prettier for TypeScript) is the source of truth.
- Keep public API response shapes stable; breaking changes need API versioning.
- Preserve OpenEnv validator parity: the `inference.py` log format
  (`[START]/[STEP]/[END]`) and the open-interval `(0,1)` score contract.
- The baseline policy must remain deterministic for a given `(task, seed, persona)`.

## Project roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md) for the phased plan and current status.

## Reporting issues

Please include: what you ran, what you expected, what happened, and the
relevant log output. For security issues, see [SECURITY.md](SECURITY.md)
instead of opening a public issue.
