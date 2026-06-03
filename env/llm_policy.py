"""Hybrid Policy - Planner + Executor architecture for email management."""

from __future__ import annotations

import time
from enum import Enum
from typing import Any

from openai import OpenAI

from env.config import chat_client_kwargs
from env.models import (
    Action,
    Observation,
)


class Strategy(Enum):
    """High-level strategies the planner can output."""

    PRIORITIZE_URGENT = "prioritize_urgent"
    BATCH_REPLY = "batch_reply"
    ESCALATE_CRITICAL = "escalate_critical"
    DEFER_LOW_VALUE = "defer_low_value"
    MONITOR = "monitor"


# Strategy prompt for planner
PLANNER_SYSTEM_PROMPT = """You are a Strategic Planner for an AI Chief of Staff helping an executive manage their inbox.

Your role is to analyze the current inbox state and output a HIGH-LEVEL STRATEGY (not specific actions).
Think of this as setting the game plan before the executor does the actual work.

Available strategies:
1. PRIORITIZE_URGENT: Focus on high-priority, high-value emails with approaching deadlines
2. BATCH_REPLY: Process multiple similar emails (e.g., all client responses) together
3. ESCALATE_CRITICAL: Immediately escalate legal/security risk emails
4. DEFER_LOW_VALUE: Defer low-priority, low-value emails to save time for critical tasks
5. MONITOR: Wait and see if more important emails arrive (conservative approach)

Consider:
- Current time remaining
- Email priorities, deadlines, and business values
- Risk tags (legal/security need immediate escalation)
- Persona preferences (strict_ceo: urgent focus, balanced: mix, chill_manager: relaxed)
- Remaining interruptions that could bring new urgent emails

Output ONLY valid JSON with these fields:
- strategy: "prioritize_urgent" | "batch_reply" | "escalate_critical" | "defer_low_value" | "monitor"
- reasoning: Brief explanation of why this strategy is optimal right now
- confidence: 0.0-1.0 (how certain you are this is the right strategy)
- key_emails: List of email IDs this strategy should focus on
"""


def _build_planner_prompt(observation: Observation) -> str:
    """Build prompt for strategic planning."""
    lines = [
        f"Time remaining: {observation.time_remaining} minutes",
        f"Current minute: {observation.current_minute}",
        f"Risk level: {observation.risk_level}",
        f"Persona: {observation.persona}",
        f"Pending actions: {len(observation.pending_actions)} emails",
        f"Remaining interruptions: {observation.remaining_interruptions}",
        "",
        "Emails in inbox:",
    ]

    for email in observation.emails:
        thread_context = ""
        if email.thread_history:
            thread_context = f" (Thread: {len(email.thread_history)} messages)"

        lines.append(
            f"\n- ID: {email.id}\n"
            f"  From: {email.sender} ({email.sender_role})\n"
            f"  Subject: {email.subject}\n"
            f"  Body: {email.body[:150]}{'...' if len(email.body) > 150 else ''}{thread_context}\n"
            f"  Priority: {email.priority_hint} | Deadline: {email.deadline_minutes}min | Value: {email.business_value}\n"
            f"  Risk tag: {email.risk_tag}"
        )

    lines.append("\nChoose the optimal strategy for this moment. Output ONLY valid JSON.")
    return "\n".join(lines)


def _parse_strategy_response(text: str) -> dict[str, Any] | None:
    """Parse LLM strategy response."""
    import json
    import re

    # Try direct JSON first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to extract JSON from markdown code blocks
    try:
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            return json.loads(match.group(1))

        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
    except (json.JSONDecodeError, AttributeError):
        pass

    return None


def _validate_strategy(strategy_dict: dict[str, Any]) -> Strategy | None:
    """Validate strategy from parsed response."""
    try:
        strategy_value = strategy_dict.get("strategy")
        if strategy_value is None:
            return None

        valid_strategies = {s.value for s in Strategy}
        if strategy_value not in valid_strategies:
            return None

        return Strategy(strategy_value)
    except Exception:
        return None


class Planner:
    """Strategic planner that outputs high-level strategies (not actions)."""

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        temperature: float = 0.3,
        timeout_seconds: float = 30.0,
    ):
        self._model = model
        self._temperature = temperature
        self._timeout_seconds = timeout_seconds
        self._client: OpenAI | None = None
        self._current_strategy: Strategy | None = None

    def _get_client(self) -> OpenAI:
        """Lazy initialization of OpenAI/Azure client."""
        if self._client is None:
            kwargs, model = chat_client_kwargs(self._timeout_seconds)
            self._client = OpenAI(**kwargs)
            if model != self._model:
                self._model = model
        return self._client

    def plan(self, observation: Observation) -> tuple[Strategy, dict[str, Any]]:
        """
        Analyze inbox and output strategic plan.

        Returns:
            tuple of (Strategy, metadata including reasoning, confidence, etc.)
        """
        start_time = time.time()

        # Check for critical emails that need immediate escalation
        for email in observation.emails:
            if email.risk_tag in {"legal", "security"}:
                return Strategy.ESCALATE_CRITICAL, {
                    "reasoning": f"Critical {email.risk_tag} risk detected - must escalate",
                    "confidence": 1.0,
                    "key_emails": [email.id],
                }

        # Check if time is running low and there are urgent emails
        if observation.time_remaining < 30:
            urgent_emails = [e for e in observation.emails if e.priority_hint == "high"]
            if urgent_emails:
                return Strategy.PRIORITIZE_URGENT, {
                    "reasoning": "Low time remaining with urgent emails pending",
                    "confidence": 0.9,
                    "key_emails": [e.id for e in urgent_emails],
                }

        # Call LLM for strategy
        try:
            client = self._get_client()
            response = client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
                    {"role": "user", "content": _build_planner_prompt(observation)},
                ],
                temperature=self._temperature,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content
            if not content:
                return self._fallback_strategy()

            strategy_dict = _parse_strategy_response(content)
            if strategy_dict is None:
                return self._fallback_strategy()

            strategy = _validate_strategy(strategy_dict)
            if strategy is None:
                return self._fallback_strategy()

            self._current_strategy = strategy
            latency_ms = int((time.time() - start_time) * 1000)

            return strategy, {
                "reasoning": strategy_dict.get("reasoning", "LLM strategy decision"),
                "confidence": strategy_dict.get("confidence", 0.5),
                "key_emails": strategy_dict.get("key_emails", []),
                "latency_ms": latency_ms,
                "model_name": self._model,
            }

        except Exception:
            return self._fallback_strategy()

    def _fallback_strategy(self) -> tuple[Strategy, dict[str, Any]]:
        """Fallback strategy when LLM fails."""
        return Strategy.PRIORITIZE_URGENT, {
            "reasoning": "LLM unavailable, using default priority strategy",
            "confidence": 0.3,
            "key_emails": [],
        }

    def get_current_strategy(self) -> Strategy | None:
        """Get the current active strategy."""
        return self._current_strategy

    def reset(self) -> None:
        """Reset planner state for new episode."""
        self._current_strategy = None


# Default planner instance
_default_planner: Planner | None = None


def get_strategy(observation: Observation) -> tuple[Strategy, dict[str, Any]]:
    """Get strategic plan from default planner."""
    global _default_planner
    if _default_planner is None:
        _default_planner = Planner()
    return _default_planner.plan(observation)


def reset_planner() -> None:
    """Reset default planner for new episode."""
    global _default_planner
    if _default_planner is not None:
        _default_planner.reset()
    _default_planner = None


class LLMPolicy:
    """Policy that delegates to LLM agent for decision-making."""

    def __init__(self):
        self._handled_ids: set[str] = set()

    def next_action(self, observation: Observation) -> Action | None:
        """Get next action from LLM agent."""
        from env.llm_agent import get_action as llm_get_action

        # Get action from LLM
        ai_response = llm_get_action(observation)

        action = ai_response.action

        # Track handled IDs to avoid duplicates
        if action.email_id:
            self._handled_ids.add(action.email_id)

        return action

    def reset(self) -> None:
        """Reset policy state for new episode."""
        from env.llm_agent import reset_agent

        self._handled_ids.clear()
        reset_agent()
