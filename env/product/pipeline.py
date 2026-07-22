"""Run a policy over a real-inbox Observation to produce proposed actions.

This is the decision loop **without** the sim's env/reward/grader. It drives the
existing :class:`env.policy.BaselinePolicy` exactly as the simulator does — call
``next_action`` until it returns ``None`` — but over a real, gold-free
Observation. The result is a list of proposed actions; whether each needs human
approval mirrors the sim's rule (``reply``/``escalate`` do).
"""

from __future__ import annotations

from dataclasses import dataclass

from env.models import Action, Observation
from env.policy import BaselinePolicy

# Actions that touch the outside world (send/forward) always pause for a human.
# Mirrors env.approval.ApprovalRequestStore.check_approval_required.
_APPROVAL_REQUIRED = {"reply", "escalate"}


@dataclass(frozen=True)
class ProposedActionDraft:
    """A provider-neutral action the copilot proposes for one message."""

    email_id: str
    action_type: str
    content: str | None = None
    escalate_to: str | None = None
    label: str | None = None
    requires_approval: bool = False


def run_policy(observation: Observation) -> list[Action]:
    """Drive BaselinePolicy over a static Observation until it's done.

    The policy tracks its own per-email state (prioritized / classified /
    handled), so repeated calls converge and eventually return ``None``. A hard
    iteration cap guards against any pathological non-termination.
    """
    policy = BaselinePolicy()
    actions: list[Action] = []
    max_iterations = len(observation.emails) * 4 + 8
    for _ in range(max_iterations):
        action = policy.next_action(observation)
        if action is None:
            break
        actions.append(action)
    return actions


def to_proposals(actions: list[Action]) -> list[ProposedActionDraft]:
    """Map policy Actions to per-message proposals.

    ``prioritize`` is an inbox-level ordering with no per-message side effect, so
    it is not turned into a proposal. ``reply``/``escalate`` are held for
    approval; ``classify``/``defer`` are internal/low-risk and auto-executable.
    """
    proposals: list[ProposedActionDraft] = []
    for action in actions:
        if action.action_type == "prioritize" or not action.email_id:
            continue
        proposals.append(
            ProposedActionDraft(
                email_id=action.email_id,
                action_type=action.action_type,
                content=action.content,
                escalate_to=action.escalate_to,
                label=action.label,
                requires_approval=action.action_type in _APPROVAL_REQUIRED,
            )
        )
    return proposals
