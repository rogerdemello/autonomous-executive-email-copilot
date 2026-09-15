"""The ceiling on model spend, and the ledger it meters against.

The property that matters is not that the cap is exact — it is that reaching it
costs *prose and nothing else*. A workspace at its budget must still triage,
verify, track commitments and send; it just stops paying a model to write. That
is the same degraded state a deployment with no API key runs in permanently, so
the test for "at budget" is that the result is indistinguishable from "no key".

The second property is attribution: two orgs drafting in the same process must
not be able to spend each other's budget.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.copilot.providers.base import FetchedMessage
from app.core.db import migrate_db
from app.core.models import TokenUsage
from app.llm.draft_cache import DraftCache, draft_key
from app.llm.providers.base import LLMProvider, LLMResponse
from app.saas.llm_budget import LlmBudget, reset_warning_state
from app.saas.repository import LlmUsageRepository, month_start_iso
from app.saas.sync_service import resolve_draft


@pytest.fixture(autouse=True)
def _schema_and_clean_warnings():
    migrate_db()
    reset_warning_state()
    yield
    reset_warning_state()


@pytest.fixture
def empty_cache(monkeypatch, tmp_path):
    """No cached prose, so the model is the only source of a draft."""
    import app.llm.draft_cache as cache_module

    cache = DraftCache(tmp_path / "drafts.json")
    monkeypatch.setattr(cache_module, "_default_cache", cache)
    return cache


def _org(name: str) -> str:
    """A real organization row — spend is keyed on one and rows are per-org."""
    from app.saas.provisioning import provision_org

    result = provision_org(
        org_name=name,
        owner_email=f"owner-{name.lower().replace(' ', '-')}@example.test",
        owner_name="Owner",
        password="correct horse battery staple",
    )
    return result["organization"]["id"]


def _message() -> FetchedMessage:
    return FetchedMessage(
        provider_message_id="m-1",
        thread_id="t-1",
        sender="priya.nair@northwind.example",
        sender_name="Priya Nair",
        subject="Billing service outage",
        body="503s since 06:20",
    )


class _Proposal:
    action_type = "reply"
    email_id = "m-1"
    content = "Generic policy sentence."
    escalate_to = None
    label = None


class _MailProvider:
    """A mailbox provider with no authored prose of its own."""


class _CountingProvider(LLMProvider):
    """An LLM that answers, and counts how many times it was asked."""

    provider_name = "counting"

    def __init__(self, cost_tokens: int = 1000) -> None:
        self.calls = 0
        self.cost_tokens = cost_tokens

    def generate(self, messages, **kwargs):  # type: ignore[override]
        self.calls += 1
        return LLMResponse(
            content=json.dumps({"body": "Declare the incident now.", "confidence": 0.8}),
            usage=TokenUsage(
                prompt_tokens=self.cost_tokens,
                completion_tokens=self.cost_tokens,
            ),
            model="gpt-4o-mini",
        )


@pytest.fixture
def drafter_provider(monkeypatch):
    """Point the process-wide drafter at a stub LLM."""
    from app.llm import drafter as drafter_module

    provider = _CountingProvider()
    monkeypatch.setattr(drafter_module, "_default_drafter", drafter_module.EmailDrafter(provider))
    return provider


# --------------------------------------------------------------------------- #
# The ledger
# --------------------------------------------------------------------------- #


def test_spend_accumulates_per_org() -> None:
    org = _org("Ledger Co")
    repo = LlmUsageRepository()

    assert repo.month_to_date(org) == 0.0

    repo.record(org_id=org, cost_usd=0.01, model="gpt-4o-mini", prompt_tokens=100)
    repo.record(org_id=org, cost_usd=0.02, model="gpt-4o-mini", completion_tokens=50)

    assert repo.month_to_date(org) == pytest.approx(0.03)
    summary = repo.summary(org)
    assert summary["calls"] == 2
    assert summary["prompt_tokens"] == 100
    assert summary["completion_tokens"] == 50


def test_one_orgs_spend_is_invisible_to_another() -> None:
    """Cross-tenant leakage in the ledger would bill the wrong customer."""
    first, second = _org("First Co"), _org("Second Co")
    repo = LlmUsageRepository()

    repo.record(org_id=first, cost_usd=5.0)

    assert repo.month_to_date(first) == pytest.approx(5.0)
    assert repo.month_to_date(second) == 0.0
    assert repo.by_org()[first] == pytest.approx(5.0)
    assert second not in repo.by_org()


def test_last_months_spend_does_not_count_against_this_month() -> None:
    """The window is a calendar month; a cap that never resets is not a cap."""
    org = _org("Rollover Co")
    repo = LlmUsageRepository()
    repo.record(org_id=org, cost_usd=99.0)

    # Read the same ledger from the following month.
    now = datetime.now(timezone.utc)
    next_month = now.replace(year=now.year + 1)

    assert repo.month_to_date(org) == pytest.approx(99.0)
    assert repo.month_to_date(org, now=next_month) == 0.0


def test_month_start_is_the_first_instant_of_the_utc_month() -> None:
    stamp = month_start_iso(datetime(2026, 9, 15, 13, 45, tzinfo=timezone.utc))
    assert stamp.startswith("2026-09-01T00:00:00")


# --------------------------------------------------------------------------- #
# The meter
# --------------------------------------------------------------------------- #


def test_a_fresh_budget_allows_and_an_exhausted_one_does_not() -> None:
    budget = LlmBudget("org-1", limit_usd=1.0, spent_usd=0.0)
    assert budget.allows()
    assert budget.remaining_usd == pytest.approx(1.0)

    budget.spent_usd = 1.0
    assert not budget.allows()
    assert budget.remaining_usd == 0.0


def test_a_zero_limit_means_unlimited() -> None:
    """`0` is the documented escape hatch, not a budget of nothing."""
    budget = LlmBudget("org-1", limit_usd=0.0, spent_usd=10_000.0)
    assert not budget.enforced
    assert budget.allows()
    assert budget.remaining_usd == float("inf")


def test_recording_advances_the_meter_and_writes_the_ledger() -> None:
    org = _org("Metered Co")
    budget = LlmBudget(org, limit_usd=1.0)

    budget.record(cost_usd=0.25, model="gpt-4o-mini", prompt_tokens=10, completion_tokens=5)

    assert budget.spent_usd == pytest.approx(0.25)
    assert LlmUsageRepository().month_to_date(org) == pytest.approx(0.25)


def test_a_budget_reads_its_starting_spend_from_the_ledger() -> None:
    """A restart mid-month must not hand the org a fresh allowance."""
    org = _org("Restarted Co")
    LlmUsageRepository().record(org_id=org, cost_usd=0.9)

    budget = LlmBudget(org, limit_usd=1.0)

    assert budget.spent_usd == pytest.approx(0.9)
    assert budget.allows()

    budget.record(cost_usd=0.2)
    assert not budget.allows()


def test_an_unlimited_budget_does_not_read_the_ledger(monkeypatch) -> None:
    """The common no-cap case should not pay for a query per sync."""

    def _explode(*args, **kwargs):
        raise AssertionError("month_to_date should not be called when uncapped")

    monkeypatch.setattr(LlmUsageRepository, "month_to_date", _explode)
    assert LlmBudget("org-1", limit_usd=0.0).allows()


def test_a_broken_ledger_read_does_not_fail_the_sync(monkeypatch) -> None:
    """Accounting is never allowed to be the reason a mailbox stops working."""

    def _explode(*args, **kwargs):
        raise RuntimeError("database is gone")

    monkeypatch.setattr(LlmUsageRepository, "month_to_date", _explode)
    monkeypatch.setattr(LlmUsageRepository, "record", _explode)

    budget = LlmBudget("org-1", limit_usd=5.0)
    assert budget.spent_usd == 0.0
    budget.record(cost_usd=1.0)  # must not raise
    assert budget.spent_usd == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# What the cap actually does to a draft
# --------------------------------------------------------------------------- #


def test_within_budget_the_model_writes_the_draft(empty_cache, drafter_provider) -> None:
    org = _org("Funded Co")
    budget = LlmBudget(org, limit_usd=100.0)

    resolved = resolve_draft(
        _MailProvider(),
        _Proposal(),
        message=_message(),
        live_llm=True,
        budget=budget,
    )

    assert resolved.source == "llm"
    assert resolved.body == "Declare the incident now."
    assert drafter_provider.calls == 1
    # And it was billed to the org that asked for it.
    assert LlmUsageRepository().month_to_date(org) > 0


def test_at_budget_the_draft_degrades_and_the_model_is_never_called(
    empty_cache, drafter_provider
) -> None:
    """The whole point: the copilot keeps working, it just stops paying to write."""
    org = _org("Capped Co")
    LlmUsageRepository().record(org_id=org, cost_usd=25.0)
    budget = LlmBudget(org, limit_usd=25.0)

    resolved = resolve_draft(
        _MailProvider(),
        _Proposal(),
        message=_message(),
        live_llm=True,
        budget=budget,
    )

    # Falls all the way back to the policy's own sentence — the same result as
    # running with no API key configured at all.
    assert resolved.source == "authored"
    assert resolved.body == "Generic policy sentence."
    assert drafter_provider.calls == 0


def test_cached_prose_is_still_served_when_the_budget_is_gone(
    empty_cache, drafter_provider
) -> None:
    """Prose already paid for costs nothing; withholding it bills the customer twice."""
    org = _org("Cached Co")
    msg = _message()
    empty_cache.put(
        draft_key(
            provider_message_id=msg.provider_message_id,
            subject=msg.subject,
            body=msg.body,
            action_type="reply",
        ),
        body="Prose from before the cap.",
        rationale=[],
        confidence=0.9,
    )
    LlmUsageRepository().record(org_id=org, cost_usd=50.0)

    resolved = resolve_draft(
        _MailProvider(),
        _Proposal(),
        message=msg,
        live_llm=True,
        budget=LlmBudget(org, limit_usd=25.0),
    )

    assert resolved.source == "llm"
    assert resolved.body == "Prose from before the cap."
    assert drafter_provider.calls == 0


def test_without_a_budget_nothing_changes(empty_cache, drafter_provider) -> None:
    """`budget=None` is the path every existing caller and test takes."""
    resolved = resolve_draft(
        _MailProvider(),
        _Proposal(),
        message=_message(),
        live_llm=True,
        budget=None,
    )
    assert resolved.source == "llm"
    assert drafter_provider.calls == 1


def test_a_sync_cannot_overshoot_by_more_than_one_call(empty_cache, drafter_provider) -> None:
    """In-memory metering is what stops 60 messages blowing a budget of one."""
    org = _org("Runaway Co")
    # Priced so a single call (2000 tokens of gpt-4o-mini) exceeds the cap.
    budget = LlmBudget(org, limit_usd=0.0000001)

    for _ in range(5):
        resolve_draft(
            _MailProvider(),
            _Proposal(),
            message=_message(),
            live_llm=True,
            budget=budget,
        )

    assert drafter_provider.calls == 1
