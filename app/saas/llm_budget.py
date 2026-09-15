"""A per-organization ceiling on model spend, metered over a calendar month.

Drafting is enabled in production and a background worker sweeps every
connected mailbox on a cadence. Without a ceiling that is an unbounded bill,
and the only accounting that existed was an in-process Prometheus counter —
not per-org, and reset by every deploy. :class:`LlmBudget` is the meter:
month-to-date spend is read once, held for the duration of a sync, and
incremented as calls are made, so a single runaway sync cannot overshoot by
more than one call.

**Reaching the cap degrades prose, never behaviour.** Triage, routing,
verification, commitment extraction and sending are all deterministic and cost
nothing; they keep working. What stops is paying a model to write the words,
which falls back to the provider-authored or generic text the copilot already
uses whenever no key is configured. That is the same degraded state a customer
with no API key runs in permanently, so it is a well-tested path rather than a
new failure mode.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Once an org is over budget, say so once a day rather than once per message.
# A 60-message sync every 15 minutes would otherwise write ~5,800 identical
# warnings a day, which is how a log stops being read.
_last_warned: dict[str, str] = {}


def reset_warning_state() -> None:
    """Forget who has been warned (tests, and after a settings change)."""
    _last_warned.clear()


class LlmBudget:
    """One org's remaining model spend for the current month.

    Constructed once per sync. ``allows()`` is a pure in-memory check after the
    single read in ``__init__``, so metering a 100-message mailbox costs one
    query rather than one per message.
    """

    def __init__(
        self,
        org_id: str,
        *,
        limit_usd: float | None = None,
        spent_usd: float | None = None,
        now: datetime | None = None,
    ) -> None:
        self.org_id = org_id
        self._now = now
        self.limit_usd = (
            float(get_settings().llm_monthly_budget_usd) if limit_usd is None else float(limit_usd)
        )
        if spent_usd is not None:
            self.spent_usd = float(spent_usd)
        elif self.limit_usd <= 0:
            # No cap means no reason to read the ledger. Recording still
            # happens — the operator view wants the number either way.
            self.spent_usd = 0.0
        else:
            self.spent_usd = self._read_spend()

    def _read_spend(self) -> float:
        from .repository import LlmUsageRepository

        try:
            return LlmUsageRepository().month_to_date(self.org_id, now=self._now)
        except Exception:  # noqa: BLE001 - a ledger read must never fail a sync
            logger.warning("Could not read LLM spend for org %s; assuming 0", self.org_id)
            return 0.0

    @property
    def enforced(self) -> bool:
        """Whether a ceiling applies at all. ``0`` means unlimited."""
        return self.limit_usd > 0

    @property
    def remaining_usd(self) -> float:
        if not self.enforced:
            return float("inf")
        return max(0.0, self.limit_usd - self.spent_usd)

    def allows(self) -> bool:
        """Is there budget left for another model call?"""
        if not self.enforced:
            return True
        if self.spent_usd < self.limit_usd:
            return True
        self._warn_once()
        return False

    def _warn_once(self) -> None:
        today = (self._now or datetime.now(timezone.utc)).date().isoformat()
        if _last_warned.get(self.org_id) == today:
            return
        _last_warned[self.org_id] = today
        logger.warning(
            "Org %s has reached its monthly model budget ($%.2f of $%.2f); "
            "drafts fall back to non-model prose until the month rolls over",
            self.org_id,
            self.spent_usd,
            self.limit_usd,
        )

    def record(
        self,
        *,
        cost_usd: float,
        model: str | None = None,
        purpose: str = "draft",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        """Bill one call to this org, in memory and in the ledger.

        The in-memory total is advanced first and unconditionally: if the write
        fails, the safe outcome is that this sync still believes it spent the
        money, not that it spends it again.
        """
        self.spent_usd += float(cost_usd or 0.0)
        from .repository import LlmUsageRepository

        try:
            LlmUsageRepository().record(
                org_id=self.org_id,
                cost_usd=cost_usd,
                model=model,
                purpose=purpose,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        except Exception:  # noqa: BLE001 - accounting must never fail a sync
            logger.warning("Could not record LLM spend for org %s", self.org_id, exc_info=True)
