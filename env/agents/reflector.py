"""Reflective self-critique agent (deterministic, provider-free).

A propose -> critique -> revise loop: it proposes an action with the deterministic
``BaselinePolicy``, critiques the proposal against the safety guardrails
(``env.safety.guardrails``), and revises unsafe proposals to a safe alternative —
within a bounded, deterministic retry cap. Because every step is deterministic and
needs no LLM provider, the agent is fully reproducible (INV-2) and is graded by the
same frozen grader as every other agent, so it cannot move the canonical goldens.

The point of the reflection pass is to improve the safety metric on adversarial
scenarios (emails carrying prompt-injection text) without sacrificing the strong
baseline coverage on normal inboxes.
"""

from __future__ import annotations

from env.models import Action, Observation
from env.policy import BaselinePolicy
from env.safety.guardrails import (
    detect_prompt_injection,
    detect_risky_content,
    is_forbidden_escalation,
)

from .base import BaseAgent


class ReflectiveAgent(BaseAgent):
    """Wraps BaselinePolicy with a deterministic safety self-critique."""

    def __init__(self, max_revisions: int = 2):
        super().__init__("ReflectiveAgent")
        self._policy = BaselinePolicy()
        self._max_revisions = max(0, max_revisions)

    @property
    def system_prompt(self) -> str:
        return (
            "I am a ReflectiveAgent. I propose an action with a deterministic heuristic, "
            "critique it against safety guardrails (prompt injection, risky content, "
            "forbidden escalation targets), and revise unsafe proposals to a safe "
            "alternative within a bounded number of revisions."
        )

    def can_handle(self, observation: Observation) -> bool:
        return bool(observation.emails)

    def execute(self, observation: Observation) -> Action | None:
        action = self._policy.next_action(observation)
        if action is None:
            return None
        for _ in range(self._max_revisions):
            issue = self._critique(action, observation)
            if issue is None:
                break
            action = self._revise(action, issue)
        return action

    def _find_email(self, observation: Observation, email_id: str | None):
        if not email_id:
            return None
        for email in observation.emails:
            if email.id == email_id:
                return email
        return None

    def _critique(self, action: Action, observation: Observation) -> str | None:
        """Return a safety issue with the proposed action, or None if it's safe."""
        if action.action_type == "reply" and detect_risky_content(action.content or ""):
            return "risky_content"
        if action.action_type == "escalate" and is_forbidden_escalation(action.escalate_to):
            return "forbidden_target"
        if action.action_type in {"reply", "escalate"}:
            email = self._find_email(observation, action.email_id)
            if email is not None and (
                detect_prompt_injection(email.subject) or detect_prompt_injection(email.body)
            ):
                return "prompt_injection"
        return None

    def _revise(self, action: Action, issue: str) -> Action:
        """Map a safety issue to a safe alternative action (deterministic)."""
        if issue == "prompt_injection":
            # Don't comply with an injection email — label it spam (resolves it safely).
            return Action(action_type="classify", email_id=action.email_id, label="spam")
        if issue == "risky_content":
            # Drop the risky reply; defer instead of sending unsafe content.
            return Action(action_type="defer", email_id=action.email_id)
        if issue == "forbidden_target":
            # Re-route to a safe internal target.
            return Action(
                action_type="escalate", email_id=action.email_id, escalate_to="chief_of_staff"
            )
        return action
