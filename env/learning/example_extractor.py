from __future__ import annotations

from typing import Any

from .trajectory_store import trajectory_store


class ExampleExtractor:
    """Extract few-shot examples from successful trajectories."""

    def extract_classification_examples(
        self,
        task_id: str,
        persona: str,
        limit: int = 2,
    ) -> list[dict[str, Any]]:
        """Extract classification action examples from high-scoring runs."""
        trajectories = trajectory_store.get_similar_trajectories(task_id, persona, limit)
        examples = []

        for traj in trajectories:
            for step in traj.get("trajectory", []):
                action = step.get("action", {})
                if action.get("action_type") == "classify":
                    examples.append({
                        "email_id": action.get("email_id"),
                        "label": action.get("label"),
                        "reason": step.get("reason", ""),
                    })
                    if len(examples) >= limit:
                        return examples

        return examples

    def extract_reply_examples(
        self,
        task_id: str,
        persona: str,
        limit: int = 2,
    ) -> list[dict[str, Any]]:
        """Extract reply action examples from high-scoring runs."""
        trajectories = trajectory_store.get_similar_trajectories(task_id, persona, limit)
        examples = []

        for traj in trajectories:
            for step in traj.get("trajectory", []):
                action = step.get("action", {})
                if action.get("action_type") == "reply":
                    examples.append({
                        "email_id": action.get("email_id"),
                        "content": action.get("content", "")[:200],
                        "sender_role": step.get("sender_role", "unknown"),
                    })
                    if len(examples) >= limit:
                        return examples

        return examples

    def extract_escalation_examples(
        self,
        task_id: str,
        persona: str,
        limit: int = 2,
    ) -> list[dict[str, Any]]:
        """Extract escalation action examples from high-scoring runs."""
        trajectories = trajectory_store.get_similar_trajectories(task_id, persona, limit)
        examples = []

        for traj in trajectories:
            for step in traj.get("trajectory", []):
                action = step.get("action", {})
                if action.get("action_type") == "escalate":
                    examples.append({
                        "email_id": action.get("email_id"),
                        "escalate_to": action.get("escalate_to"),
                        "reason": step.get("reason", ""),
                    })
                    if len(examples) >= limit:
                        return examples

        return examples

    def extract_prioritization_examples(
        self,
        task_id: str,
        persona: str,
        limit: int = 2,
    ) -> list[dict[str, Any]]:
        """Extract prioritization examples from high-scoring runs."""
        trajectories = trajectory_store.get_similar_trajectories(task_id, persona, limit)
        examples = []

        for traj in trajectories:
            for step in traj.get("trajectory", []):
                action = step.get("action", {})
                if action.get("action_type") == "prioritize":
                    examples.append({
                        "priority_order": action.get("priority_order", []),
                        "reason": step.get("reason", ""),
                    })
                    if len(examples) >= limit:
                        return examples

        return examples

    def extract_all_examples(
        self,
        task_id: str,
        persona: str,
    ) -> dict[str, list[dict[str, Any]]]:
        """Extract all types of examples for a task/persona combination."""
        return {
            "classify": self.extract_classification_examples(task_id, persona),
            "reply": self.extract_reply_examples(task_id, persona),
            "escalate": self.extract_escalation_examples(task_id, persona),
            "prioritize": self.extract_prioritization_examples(task_id, persona),
        }


example_extractor = ExampleExtractor()