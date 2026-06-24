<!--
Thanks for contributing to the Autonomous Executive Email Copilot!
Please fill out the sections below. See CONTRIBUTING.md before opening.
-->

## Summary

<!-- What does this PR do and why? Link any related issue, e.g. "Closes #123". -->

## Type of change

- [ ] Bug fix (non-breaking change that fixes an issue)
- [ ] New feature (non-breaking change that adds behavior)
- [ ] Breaking change (would change an existing API response shape — needs versioning)
- [ ] Docs / chore / CI (no runtime behavior change)

## Checklist

Run these locally before requesting review. CI (`.github/workflows/ci.yml`)
gates the same checks on every push and pull request.

- [ ] **Lint:** `ruff check .` and `ruff format --check .` are clean.
- [ ] **Tests:** `python -m pytest -q` passes (the suite stays green).
- [ ] **No new deprecation warnings:** `python -m pytest -q -W error::DeprecationWarning`.
- [ ] **Coverage gate:** total coverage stays at or above the 78% threshold enforced by CI.
- [ ] **Types (mypy):** `mypy env --ignore-missing-imports --no-strict-optional` — informational, but no new errors introduced.
- [ ] **Security (informational):** no new `bandit` findings on changed code; `pip-audit` reviewed if dependencies changed.
- [ ] **Frontend (if `dashboard/` changed):** `npm run lint`, `npm run format:check`, `npm test`, `npx tsc --noEmit`, and `npm run build` all pass.
- [ ] **Docker smoke (if runtime/Dockerfile changed):** `docker build .` succeeds and `/health`, `/docs`, `/dashboard/` respond.
- [ ] **Tests added/updated** for the behavior changed — every documented claim is backed by a test.
- [ ] **Docs updated** (README is owned by maintainers; update `docs/` and `CHANGELOG.md` as appropriate).

## Invariants

Confirm this PR preserves the project invariants (see CONTRIBUTING.md):

- [ ] Score/log contract is intact: `inference.py` log format (`[START]/[STEP]/[END]`) and the open-interval `(0,1)` score contract.
- [ ] The baseline policy remains deterministic for a given `(task, seed, persona)`.
- [ ] No existing API response shapes changed without versioning.
- [ ] No new task ids introduced (the grader only supports `easy_classification`, `medium_prioritization`, `hard_full_management`).

## Notes for reviewers

<!-- Anything reviewers should focus on, known limitations, or follow-ups. -->
