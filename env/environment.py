from __future__ import annotations

from .models import (
    Action,
    ActionResult,
    EmailRecord,
    Observation,
    ObservationEmail,
    PersonaType,
    Scenario,
    StateSnapshot,
)
from .tasks import build_scenario
from .utils import (
    compute_gold_priority_order,
    compute_pending_actions,
    derive_risk_level,
    get_action_cost_minutes,
    get_persona_profile,
    ranking_similarity,
    reply_keyword_score,
    strict_unit_interval,
)


class ExecutiveEmailEnv:
    def __init__(self, task_id: str = "hard_full_management", seed: int = 42, persona: PersonaType = "balanced"):
        self._task_id = task_id
        self._seed = seed
        self._persona = persona
        self._persona_profile = get_persona_profile(persona)
        self._scenario: Scenario | None = None
        self._emails: list[EmailRecord] = []
        self._pending_interruptions = []
        self._time_remaining = 0
        self._current_minute = 0
        self._risk_level = "medium"
        self._action_history: list[Action] = []
        self._total_reward = 0.0
        self._last_priority_order: list[str] = []
        self._best_priority_similarity = 0.0
        self._deadline_penalized_ids: set[str] = set()
        self.reset(task_id=task_id, seed=seed, persona=persona)

    def reset(
        self,
        task_id: str | None = None,
        seed: int | None = None,
        persona: PersonaType | None = None,
    ) -> Observation:
        if task_id is not None:
            self._task_id = task_id
        if seed is not None:
            self._seed = seed
        if persona is not None:
            self._persona = persona
        self._persona_profile = get_persona_profile(self._persona)

        self._scenario = build_scenario(task_id=self._task_id, seed=self._seed, persona=self._persona)
        self._emails = [email.model_copy(deep=True) for email in self._scenario.emails]
        self._pending_interruptions = [event.model_copy(deep=True) for event in self._scenario.interruptions]
        self._time_remaining = self._scenario.time_budget
        self._current_minute = 0
        self._risk_level = self._scenario.risk_level
        self._action_history = []
        self._total_reward = 0.0
        self._last_priority_order = []
        self._best_priority_similarity = 0.0
        self._deadline_penalized_ids = set()
        return self._build_observation()

    def state(self) -> StateSnapshot:
        return StateSnapshot(
            task_id=self._task_id,
            seed=self._seed,
            persona=self._persona,
            time_remaining=self._time_remaining,
            current_minute=self._current_minute,
            risk_level=self._risk_level,
            emails=[email.model_copy(deep=True) for email in self._emails],
            action_history=[action.model_copy(deep=True) for action in self._action_history],
            total_reward=round(self._total_reward, 6),
            remaining_interruptions=len(self._pending_interruptions),
        )

    def step(self, action: Action) -> ActionResult:
        if self._is_done():
            return ActionResult(
                observation=self._build_observation(),
                reward=0.0,
                done=True,
                info={"message": "Episode already complete."},
            )

        reward = 0.0
        info: dict[str, object] = {}

        cost = get_action_cost_minutes().get(action.action_type, 1)
        self._advance_time(cost)
        arrived = self._inject_interruptions()
        if arrived:
            info["interruptions"] = arrived

        if action.action_type == "classify":
            reward += self._handle_classify(action, info)
        elif action.action_type == "prioritize":
            reward += self._handle_prioritize(action, info)
        elif action.action_type == "reply":
            reward += self._handle_reply(action, info)
        elif action.action_type == "escalate":
            reward += self._handle_escalate(action, info)
        elif action.action_type == "defer":
            reward += self._handle_defer(action, info)
        else:
            reward -= 0.1 * self._persona_profile.redundant_penalty_multiplier
            info["error"] = "Unsupported action_type"

        reward += self._apply_deadline_penalties()
        reward = max(-1.0, min(1.0, reward))

        self._action_history.append(action)
        self._risk_level = derive_risk_level(self._emails)
        self._total_reward += reward

        done = self._is_done()
        if done:
            terminal_penalty = self._terminal_penalty()
            if terminal_penalty != 0.0:
                reward = max(-1.0, min(1.0, reward + terminal_penalty))
                self._total_reward += terminal_penalty
                info["terminal_penalty"] = round(terminal_penalty, 3)

        return ActionResult(
            observation=self._build_observation(),
            reward=round(reward, 6),
            done=done,
            info=info,
        )

    def metrics(self) -> dict[str, float]:
        classification_accuracy = 0.0
        if self._emails:
            correct = sum(1 for e in self._emails if e.predicted_label == e.expected_label)
            classification_accuracy = correct / len(self._emails)

        actionable = [e for e in self._emails if e.expected_action != "ignore"]
        action_correctness = 0.0
        if actionable:
            correct_actions = sum(1 for e in actionable if e.handled_action == e.expected_action)
            action_correctness = correct_actions / len(actionable)

        reply_targets = [e for e in self._emails if e.expected_action == "reply"]
        reply_quality = 0.0
        if reply_targets:
            values: list[float] = []
            for email in reply_targets:
                if email.handled_action != "reply":
                    values.append(0.0)
                else:
                    values.append(reply_keyword_score(email.last_reply, email.expected_reply_keywords))
            reply_quality = sum(values) / len(values)

        prioritization = self._best_priority_similarity

        resolved_ratio = 0.0
        non_spam = [e for e in self._emails if e.expected_label != "spam"]
        if non_spam:
            resolved_ratio = sum(1 for e in non_spam if e.resolved) / len(non_spam)

        return {
            "classification_accuracy": strict_unit_interval(classification_accuracy),
            "action_correctness": strict_unit_interval(action_correctness),
            "response_quality": strict_unit_interval(reply_quality),
            "prioritization": strict_unit_interval(prioritization),
            "resolved_ratio": strict_unit_interval(resolved_ratio),
        }

    def _build_observation(self) -> Observation:
        emails = [
            ObservationEmail(
                id=e.id,
                sender=e.sender,
                sender_role=e.sender_role,
                subject=e.subject,
                body=e.body,
                priority_hint=e.priority_hint,
                deadline_minutes=e.deadline_minutes,
                business_value=e.business_value,
                risk_tag=e.risk_tag,
                thread_history=e.thread_history,
            )
            for e in self._emails
        ]
        return Observation(
            emails=emails,
            time_remaining=self._time_remaining,
            pending_actions=compute_pending_actions(self._emails),
            risk_level=self._risk_level,
            current_minute=self._current_minute,
            persona=self._persona,
            remaining_interruptions=len(self._pending_interruptions),
        )

    def _find_email(self, email_id: str | None) -> EmailRecord | None:
        if not email_id:
            return None
        for email in self._emails:
            if email.id == email_id:
                return email
        return None

    def _advance_time(self, cost: int) -> None:
        self._time_remaining = max(0, self._time_remaining - cost)
        self._current_minute += cost
        for email in self._emails:
            if not email.resolved:
                email.deadline_minutes -= cost

    def _handle_classify(self, action: Action, info: dict[str, object]) -> float:
        email = self._find_email(action.email_id)
        if email is None or action.label is None:
            info["error"] = "classify requires email_id and label"
            return -0.1 * self._persona_profile.redundant_penalty_multiplier

        if email.predicted_label is not None:
            return -0.1 * self._persona_profile.redundant_penalty_multiplier

        email.predicted_label = action.label
        is_correct = action.label == email.expected_label
        info["classification_correct"] = is_correct

        if action.label == "spam":
            email.resolved = True

        return 0.2 if is_correct else 0.0

    def _handle_prioritize(self, action: Action, info: dict[str, object]) -> float:
        if not action.priority_order:
            info["error"] = "prioritize requires priority_order"
            return -0.1 * self._persona_profile.redundant_penalty_multiplier

        self._last_priority_order = action.priority_order
        gold = compute_gold_priority_order([e for e in self._emails if not e.resolved])
        similarity = ranking_similarity(action.priority_order, gold)
        self._best_priority_similarity = max(self._best_priority_similarity, similarity)
        info["ranking_similarity"] = round(similarity, 4)
        return 0.3 * similarity

    def _handle_reply(self, action: Action, info: dict[str, object]) -> float:
        email = self._find_email(action.email_id)
        if email is None or not action.content:
            info["error"] = "reply requires email_id and content"
            return -0.1 * self._persona_profile.redundant_penalty_multiplier

        if email.resolved:
            return -0.1 * self._persona_profile.redundant_penalty_multiplier

        email.handled_action = "reply"
        email.last_reply = action.content
        email.resolved = True

        quality = reply_keyword_score(action.content, email.expected_reply_keywords)
        reward = 0.5 * quality
        info["reply_quality"] = round(quality, 4)

        if email.expected_action != "reply":
            if email.critical:
                reward -= 1.0
            else:
                reward -= 0.2

        return reward

    def _handle_escalate(self, action: Action, info: dict[str, object]) -> float:
        email = self._find_email(action.email_id)
        if email is None:
            info["error"] = "escalate requires email_id"
            return -0.1 * self._persona_profile.redundant_penalty_multiplier

        if email.resolved:
            return -0.1 * self._persona_profile.redundant_penalty_multiplier

        email.handled_action = "escalate"
        email.resolved = True

        if email.expected_action == "escalate":
            reward = 0.4
            if email.recommended_escalation and action.escalate_to == email.recommended_escalation:
                reward += 0.1
            return reward

        if email.expected_action == "reply" and email.critical:
            return -0.3

        return -0.1

    def _handle_defer(self, action: Action, info: dict[str, object]) -> float:
        email = self._find_email(action.email_id)
        if email is None:
            info["error"] = "defer requires email_id"
            return -0.1 * self._persona_profile.redundant_penalty_multiplier

        if email.resolved:
            return -0.1 * self._persona_profile.redundant_penalty_multiplier

        email.handled_action = "defer"

        if email.expected_action == "defer":
            email.resolved = True
            return 0.1

        if email.expected_label == "urgent":
            return -0.7 * self._persona_profile.urgent_defer_penalty_multiplier

        return -0.1 * self._persona_profile.redundant_penalty_multiplier

    def _apply_deadline_penalties(self) -> float:
        penalty = 0.0
        for email in self._emails:
            if email.id in self._deadline_penalized_ids:
                continue
            if email.expected_label == "urgent" and not email.resolved and email.deadline_minutes <= 0:
                penalty -= 0.7 * self._persona_profile.deadline_penalty_multiplier
                self._deadline_penalized_ids.add(email.id)
        return penalty

    def _terminal_penalty(self) -> float:
        penalty = 0.0
        unresolved_urgent = [e for e in self._emails if e.expected_label == "urgent" and not e.resolved]
        unresolved_critical = [e for e in self._emails if e.critical and not e.resolved]

        penalty -= 0.5 * len(unresolved_urgent)

        high_risk_unresolved = [e for e in unresolved_critical if e.risk_tag in {"legal", "security"}]
        penalty -= 0.4 * len(high_risk_unresolved)
        return penalty * self._persona_profile.terminal_penalty_multiplier

    def _inject_interruptions(self) -> list[str]:
        if not self._pending_interruptions:
            return []

        arrived_ids: list[str] = []
        remaining_events = []
        for event in self._pending_interruptions:
            if event.trigger_minute <= self._current_minute:
                self._emails.append(event.email.model_copy(deep=True))
                arrived_ids.append(event.email.id)
            else:
                remaining_events.append(event)

        self._pending_interruptions = remaining_events
        return arrived_ids

    def _is_done(self) -> bool:
        if self._time_remaining <= 0:
            return True

        if self._pending_interruptions:
            return False

        pending = compute_pending_actions(self._emails)
        return len(pending) == 0
