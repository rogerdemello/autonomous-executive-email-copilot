"""Map a provider-neutral RawEmail into the un-privileged Observation schema.

Sim-only fields the real world doesn't provide (priority, deadline, business value,
risk) get safe, neutral defaults — an agent must infer importance from the content,
exactly as a human would. Critically, this emits ONLY ``ObservationEmail`` /
``Observation`` (no ``EmailRecord``, no ``expected_*`` gold fields), so real mail is
ungradeable by construction (INV-5).
"""

from __future__ import annotations

from env.models import Observation, ObservationEmail, ThreadEntry

from .base import RawEmail

# Neutral defaults for fields a real inbox doesn't carry.
_DEFAULT_PRIORITY = "medium"
_DEFAULT_DEADLINE_MINUTES = 240
_DEFAULT_BUSINESS_VALUE = 0.5
_DEFAULT_RISK_TAG = "none"
_DEFAULT_SENDER_ROLE = "unknown"


def to_observation_email(raw: RawEmail) -> ObservationEmail:
    """Map one RawEmail to an ObservationEmail with neutral, un-privileged defaults."""
    thread_history = [ThreadEntry(from_address=ref, text="") for ref in raw.references]
    return ObservationEmail(
        id=raw.id,
        sender=raw.sender,
        sender_role=_DEFAULT_SENDER_ROLE,
        subject=raw.subject,
        body=raw.body,
        priority_hint=_DEFAULT_PRIORITY,
        deadline_minutes=_DEFAULT_DEADLINE_MINUTES,
        business_value=_DEFAULT_BUSINESS_VALUE,
        risk_tag=_DEFAULT_RISK_TAG,
        thread_history=thread_history,
    )


def to_observation(raws: list[RawEmail], *, time_remaining: int = 240) -> Observation:
    """Assemble an Observation from fetched RawEmails (no interruptions, low risk)."""
    emails = [to_observation_email(r) for r in raws]
    return Observation(
        emails=emails,
        time_remaining=time_remaining,
        pending_actions=[e.id for e in emails],
        risk_level="low",
        current_minute=0,
        persona="balanced",
        remaining_interruptions=0,
    )
