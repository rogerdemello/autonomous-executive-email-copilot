"""Real-inbox product pipeline: read a connected mailbox, decide, act.

This package is the runtime that turns a *connected* mailbox into copilot actions
— the half the benchmark/simulation never had. It is deliberately:

- **Gold-free.** It only ever produces :class:`env.models.Observation` /
  :class:`env.models.Action`; it never constructs ``EmailRecord`` or any
  ``expected_*`` label, and never computes a score. Real mail is ungradeable by
  construction, so quality signal comes from human approve/reject outcomes, not
  the grader.
- **Provider- and tenant-neutral.** It knows nothing about OAuth tokens, orgs, or
  the database. The SaaS layer (``env.saas``) supplies an authenticated
  :class:`~env.product.providers.base.MailProvider` and persists results.
- **Import-isolated.** It may reuse ``env.policy`` / ``env.utils`` /
  ``env.models`` but must NOT import ``env.saas.*``, ``env.grader``, or
  ``env.environment`` — mirroring the isolation of ``env.connectors`` and keeping
  the deterministic benchmark untouched. ``tests/test_product_isolation.py``
  enforces this.
"""

from __future__ import annotations

__all__ = ["providers", "enrich", "pipeline"]
