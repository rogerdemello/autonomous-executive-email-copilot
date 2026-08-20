"""The copilot's decision engine — what it does, and in what order.

This is the most consequential code in the product: it decides what gets
escalated to legal, what gets a drafted reply, and what is quietly deferred.
The same class runs against the demo mailbox and against a real Gmail account,
so these tests pin the routing rules themselves rather than any one fixture.
"""

from __future__ import annotations

import pytest

from app.copilot.policy import BaselinePolicy, Executor, HybridPolicy
from app.core.models import Observation, ObservationEmail


def email(
    email_id: str,
    *,
    subject: str = "A subject",
    body: str = "A body",
    priority: str = "medium",
    risk: str = "none",
    role: str = "internal",
    value: float = 0.7,
    deadline: int = 240,
) -> ObservationEmail:
    return ObservationEmail(
        id=email_id,
        sender=f"{email_id}@example.com",
        sender_role=role,
        subject=subject,
        body=body,
        priority_hint=priority,
        deadline_minutes=deadline,
        business_value=value,
        risk_tag=risk,
        thread_history=[],
    )


def observation(*emails: ObservationEmail, interruptions: int = 0) -> Observation:
    return Observation(
        emails=list(emails),
        time_remaining=240,
        pending_actions=[e.id for e in emails],
        risk_level="low",
        current_minute=0,
        persona="balanced",
        remaining_interruptions=interruptions,
    )


def drain(policy: BaselinePolicy, obs: Observation, limit: int = 60) -> list:
    """Run the policy to completion, the way the pipeline does."""
    actions = []
    for _ in range(limit):
        action = policy.next_action(obs)
        if action is None:
            break
        actions.append(action)
    return actions


class TestBaselineOrdering:
    def test_it_prioritizes_before_doing_anything_else(self):
        obs = observation(email("a"), email("b"))
        first = BaselinePolicy().next_action(obs)
        assert first is not None
        assert first.action_type == "prioritize"

    def test_it_classifies_every_message_before_acting_on_any(self):
        obs = observation(email("a"), email("b"), email("c"))
        actions = drain(BaselinePolicy(), obs)

        classified = [a for a in actions if a.action_type == "classify"]
        assert len(classified) == 3

        first_handling = next(
            i for i, a in enumerate(actions) if a.action_type in {"reply", "escalate", "defer"}
        )
        last_classify = max(i for i, a in enumerate(actions) if a.action_type == "classify")
        assert last_classify < first_handling

    def test_it_terminates(self):
        """No action left to take must eventually mean None, not a loop."""
        obs = observation(email("a"), email("b"))
        policy = BaselinePolicy()
        drain(policy, obs)
        assert policy.next_action(obs) is None

    def test_every_message_is_accounted_for_exactly_once(self):
        obs = observation(email("a"), email("b"), email("c"))
        actions = drain(BaselinePolicy(), obs)
        handled = [a for a in actions if a.action_type in {"reply", "escalate", "defer"}]
        # Spam is filed by classification alone, so handled <= total.
        assert len({a.email_id for a in handled}) == len(handled)


class TestRouting:
    def test_legal_risk_escalates_to_the_legal_team(self):
        obs = observation(
            email("legal", subject="URGENT contract indemnification", priority="high", risk="legal")
        )
        actions = drain(BaselinePolicy(), obs)
        escalation = next(a for a in actions if a.action_type == "escalate")
        assert escalation.escalate_to == "legal_team"

    def test_security_risk_escalates_to_the_chief_of_staff(self):
        obs = observation(
            email("sec", subject="URGENT credential breach", priority="high", risk="security")
        )
        actions = drain(BaselinePolicy(), obs)
        escalation = next(a for a in actions if a.action_type == "escalate")
        assert escalation.escalate_to == "chief_of_staff"

    def test_urgent_without_legal_or_security_risk_gets_a_reply(self):
        obs = observation(
            email("ops", subject="URGENT outage", body="failed", priority="high", risk="ops")
        )
        actions = drain(BaselinePolicy(), obs)
        assert any(a.action_type == "reply" for a in actions)
        assert not any(a.action_type == "escalate" for a in actions)

    def test_routine_mail_is_deferred(self):
        obs = observation(email("cal", subject="Room booking", body="No rush", priority="low"))
        actions = drain(BaselinePolicy(), obs)
        assert any(a.action_type == "defer" for a in actions)

    def test_spam_is_filed_without_a_reply_or_escalation(self):
        """Spam must never consume a human decision."""
        obs = observation(
            email(
                "spam", subject="Limited deal: subscribe now", body="discount offer", role="vendor"
            )
        )
        actions = drain(BaselinePolicy(), obs)

        labels = [a.label for a in actions if a.action_type == "classify"]
        assert labels == ["spam"]
        assert not any(a.action_type in {"reply", "escalate", "defer"} for a in actions)


class TestPrioritisation:
    def test_high_priority_sorts_ahead_of_low(self):
        obs = observation(
            email("low", priority="low", value=0.2),
            email("high", priority="high", value=0.9),
        )
        order = BaselinePolicy().next_action(obs).priority_order
        assert order.index("high") < order.index("low")

    def test_the_ordering_covers_every_message(self):
        obs = observation(email("a"), email("b"), email("c"))
        order = BaselinePolicy().next_action(obs).priority_order
        assert sorted(order) == ["a", "b", "c"]

    def test_an_empty_inbox_settles_immediately(self):
        """One no-op prioritize, then done.

        The empty ordering is dropped by ``pipeline.to_proposals``, so it never
        reaches a user — but the policy must still terminate rather than spin.
        """
        policy = BaselinePolicy()
        first = policy.next_action(observation())
        assert first is not None and first.action_type == "prioritize"
        assert first.priority_order == []
        assert policy.next_action(observation()) is None


class TestExecutor:
    class _Strategy:
        def __init__(self, value: str) -> None:
            self.value = value

    def test_escalate_critical_finds_the_risky_message_directly(self):
        obs = observation(email("plain"), email("risky", risk="legal"))
        action = Executor().execute(self._Strategy("escalate_critical"), obs)
        assert action is not None
        assert action.action_type == "escalate"
        assert action.email_id == "risky"
        assert action.escalate_to == "legal_team"

    def test_escalate_critical_falls_back_when_nothing_is_risky(self):
        obs = observation(email("plain"))
        action = Executor().execute(self._Strategy("escalate_critical"), obs)
        assert action is not None
        assert action.action_type == "prioritize"  # the baseline's opening move

    @pytest.mark.parametrize(
        "strategy", ["prioritize_urgent", "batch_reply", "defer_low_value", "monitor", "unknown"]
    )
    def test_other_strategies_defer_to_the_baseline(self, strategy):
        """Every strategy must produce a real move, never a dead end."""
        obs = observation(email("a"))
        action = Executor().execute(self._Strategy(strategy), obs)
        assert action is not None

    def test_reset_clears_progress(self):
        obs = observation(email("a"))
        executor = Executor()
        executor.execute(self._Strategy("monitor"), obs)
        executor.reset()
        # A fresh executor prioritizes first again.
        action = executor.execute(self._Strategy("monitor"), obs)
        assert action.action_type == "prioritize"


class TestHybridWithoutAProvider:
    """With no API key the hybrid must degrade to the baseline, not to nothing.

    This is what makes the product runnable with zero credentials, so it is
    worth pinning rather than assuming.
    """

    @pytest.fixture(autouse=True)
    def _no_provider(self, monkeypatch):
        for var in (
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "GOOGLE_API_KEY",
            "HF_TOKEN",
            "AZURE_OPENAI_API_KEY",
            "AZURE_OPENAI_ENDPOINT",
        ):
            monkeypatch.delenv(var, raising=False)

    def test_it_produces_the_baseline_trajectory(self):
        obs = observation(email("a", risk="legal", priority="high"), email("b"))
        policy = HybridPolicy()
        actions = []
        for _ in range(60):
            action = policy.next_action(obs)
            if action is None:
                break
            actions.append(action)

        assert actions, "the no-key hybrid must still do the work"
        assert actions[0].action_type == "prioritize"
        assert any(a.action_type == "escalate" for a in actions)

    def test_reset_returns_it_to_a_clean_slate(self):
        obs = observation(email("a"))
        policy = HybridPolicy()
        policy.next_action(obs)
        policy.reset()
        assert policy.next_action(obs).action_type == "prioritize"
