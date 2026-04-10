"""LLM Policy - wraps LLMAgent for use in baseline runner."""

from __future__ import annotations

from typing import Any

from env.llm_agent import get_action as llm_get_action, reset_agent
from env.models import Action, Observation


class LLMPolicy:
    """Policy that delegates to LLM agent for decision-making."""

    def __init__(self):
        self._handled_ids: set[str] = set()

    def next_action(self, observation: Observation) -> Action | None:
        """Get next action from LLM agent."""
        # Get action from LLM
        ai_response = llm_get_action(observation)
        
        action = ai_response.action
        
        # Track handled IDs to avoid duplicates
        if action.email_id:
            self._handled_ids.add(action.email_id)
        
        return action

    def reset(self) -> None:
        """Reset policy state for new episode."""
        self._handled_ids.clear()
        reset_agent()
