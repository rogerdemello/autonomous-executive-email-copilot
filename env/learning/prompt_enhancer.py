from __future__ import annotations

from typing import Any

from .example_extractor import example_extractor


class PromptEnhancer:
    """Enhance prompts with few-shot examples from successful trajectories."""

    def __init__(self, max_examples_per_action: int = 2) -> None:
        self.max_examples_per_action = max_examples_per_action

    def enhance_system_prompt(
        self,
        base_prompt: str,
        task_id: str,
        persona: str,
    ) -> str:
        """Add few-shot examples to system prompt based on context."""
        examples = example_extractor.extract_all_examples(task_id, persona)

        if not any(examples.values()):
            return base_prompt

        enhancement_lines = [
            "",
            "## Few-shot examples from successful runs:",
        ]

        for action_type, exs in examples.items():
            if not exs:
                continue
            enhancement_lines.append(f"\n### {action_type.capitalize()} examples:")
            for i, ex in enumerate(exs[:self.max_examples_per_action], 1):
                if action_type == "classify":
                    enhancement_lines.append(
                        f"  {i}. Email {ex['email_id']} -> {ex['label']}"
                    )
                elif action_type == "reply":
                    enhancement_lines.append(
                        f"  {i}. Email {ex['email_id']}: {ex['content'][:80]}..."
                    )
                elif action_type == "escalate":
                    enhancement_lines.append(
                        f"  {i}. Email {ex['email_id']} -> {ex['escalate_to']}"
                    )
                elif action_type == "prioritize":
                    enhancement_lines.append(
                        f"  {i}. Order: {', '.join(ex['priority_order'][:3])}"
                    )

        return base_prompt + "\n".join(enhancement_lines)

    def get_examples_for_action(
        self,
        task_id: str,
        persona: str,
        action_type: str,
    ) -> list[dict[str, Any]]:
        """Get examples for a specific action type."""
        examples = example_extractor.extract_all_examples(task_id, persona)
        return examples.get(action_type, [])[:self.max_examples_per_action]

    def has_examples(self, task_id: str, persona: str) -> bool:
        """Check if there are any examples available for task/persona."""
        examples = example_extractor.extract_all_examples(task_id, persona)
        return any(examples.values())


prompt_enhancer = PromptEnhancer()