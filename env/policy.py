from __future__ import annotations

from dataclasses import dataclass, field

from env.models import Action, Observation
from env.utils import classify_heuristic


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


class Executor:
    def __init__(self):
        self._baseline = BaselinePolicy()

    def execute(
        self,
        strategy,
        observation: Observation,
    ) -> Action | None:
        if strategy.value == "escalate_critical":
            return self._execute_escalate_critical(observation)
        elif strategy.value == "prioritize_urgent":
            return self._execute_prioritize_urgent(observation)
        elif strategy.value == "batch_reply":
            return self._execute_batch_reply(observation)
        elif strategy.value == "defer_low_value":
            return self._execute_defer_low_value(observation)
        else:
            return self._fallback_to_baseline(observation)

    def _execute_escalate_critical(self, observation: Observation) -> Action | None:
        for email in observation.emails:
            if email.risk_tag in {"legal", "security"}:
                target = "legal_team" if email.risk_tag == "legal" else "chief_of_staff"
                return Action(action_type="escalate", email_id=email.id, escalate_to=target)
        return self._fallback_to_baseline(observation)

    def _execute_prioritize_urgent(self, observation: Observation) -> Action | None:
        # The strategy only sets the opening move (an explicit re-prioritization);
        # the rest of the inbox work (classify, escalate, reply, defer) is carried
        # by the strong baseline heuristics so coverage stays competitive.
        return self._fallback_to_baseline(observation)

    def _execute_batch_reply(self, observation: Observation) -> Action | None:
        return self._fallback_to_baseline(observation)

    def _execute_defer_low_value(self, observation: Observation) -> Action | None:
        return self._fallback_to_baseline(observation)

    def _fallback_to_baseline(self, observation: Observation) -> Action | None:
        return self._baseline.next_action(observation)

    def reset(self) -> None:
        self._baseline = BaselinePolicy()
        self._did_prioritize = False


class HybridPolicy:
    def __init__(self, planner_interval: int = 3):
        self._planner_interval = planner_interval
        self._executor = Executor()
        self._step_count = 0
        self._current_strategy = None
        self._strategy_metadata = {}
        # Strong deterministic fallback used whenever no LLM provider is
        # configured. Reusing BaselinePolicy verbatim keeps the no-key hybrid
        # trajectory competitive with the baseline instead of degenerating.
        self._fallback = BaselinePolicy()

    def next_action(self, observation: Observation) -> Action | None:
        from env.llm_policy import llm_provider_available

        # No provider key: run the strong baseline heuristics directly. The
        # planner would only ever emit its fallback strategy here, so skipping it
        # avoids weak strategy-specific moves while still requiring no key.
        if not llm_provider_available():
            return self._fallback.next_action(observation)

        self._step_count += 1

        if self._step_count % self._planner_interval == 1:
            from env.llm_policy import get_strategy

            strategy, metadata = get_strategy(observation)
            self._current_strategy = strategy
            self._strategy_metadata = metadata

        if self._current_strategy is None:
            from env.llm_policy import get_strategy

            strategy, metadata = get_strategy(observation)
            self._current_strategy = strategy
            self._strategy_metadata = metadata

        return self._executor.execute(self._current_strategy, observation)

    def reset(self) -> None:
        self._step_count = 0
        self._current_strategy = None
        self._strategy_metadata = {}
        self._executor.reset()
        self._fallback = BaselinePolicy()
