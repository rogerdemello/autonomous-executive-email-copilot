from __future__ import annotations

from dataclasses import dataclass, field

from .models import Action, Observation
from .utils import classify_heuristic


@dataclass
class BaselinePolicy:
    did_prioritize: bool = False
    predicted_labels: dict[str, str] = field(default_factory=dict)
    handled_ids: set[str] = field(default_factory=set)

    def next_action(self, observation: Observation) -> Action | None:
        if not self.did_prioritize:
            order = sorted(
                observation.emails,
                key=lambda e: (
                    e.priority_hint == "high",
                    e.business_value,
                    -e.deadline_minutes,
                ),
                reverse=True,
            )
            self.did_prioritize = True
            return Action(
                action_type="prioritize",
                priority_order=[email.id for email in order],
            )

        for email in observation.emails:
            if email.id not in self.predicted_labels:
                label = classify_heuristic(
                    subject=email.subject,
                    body=email.body,
                    priority_hint=email.priority_hint,
                    risk_tag=email.risk_tag,
                )
                self.predicted_labels[email.id] = label
                return Action(action_type="classify", email_id=email.id, label=label)

        ranked = sorted(
            observation.emails,
            key=lambda e: (e.priority_hint == "high", -e.deadline_minutes, e.business_value),
            reverse=True,
        )

        for email in ranked:
            if email.id in self.handled_ids:
                continue
            label = self.predicted_labels.get(email.id, "normal")
            if label == "spam":
                self.handled_ids.add(email.id)
                continue

            if label == "urgent" and email.risk_tag in {"legal", "security"}:
                self.handled_ids.add(email.id)
                target = "legal_team" if email.risk_tag == "legal" else "chief_of_staff"
                return Action(action_type="escalate", email_id=email.id, escalate_to=target)

            if label == "urgent":
                self.handled_ids.add(email.id)
                content = (
                    "Acknowledged. We are treating this as urgent and will share a concrete "
                    "timeline with mitigation details shortly."
                )
                return Action(action_type="reply", email_id=email.id, content=content)

            self.handled_ids.add(email.id)
            return Action(action_type="defer", email_id=email.id)

        if observation.remaining_interruptions > 0 and observation.emails:
            keepalive_order = [email.id for email in ranked]
            return Action(action_type="prioritize", priority_order=keepalive_order)

        return None
