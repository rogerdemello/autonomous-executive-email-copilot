"""LLM Agent for AI Chief of Staff - integrates OpenAI API with strict validation and fallback."""

from __future__ import annotations

import json
import os
import time
from typing import Any

from openai import OpenAI

from .models import (
    AIResponse,
    AIDecisionTrace,
    Action,
    AIStatusType,
    Observation,
)


# Default configuration
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TEMPERATURE = 0.2
DEFAULT_TIMEOUT_SECONDS = 30.0

# System prompt for AI Chief of Staff
SYSTEM_PROMPT = """You are an AI Chief of Staff helping an executive manage their inbox efficiently.

Your role is to make optimal email management decisions based on:
- Email priority (high/medium/low)
- Business value (0-1 scale)
- Deadline urgency
- Risk level
- Persona preferences (strict_ceo/balanced/chill_manager)

Available actions:
1. CLASSIFY: Label email as spam, normal, or urgent
2. PRIORITIZE: Order emails by importance
3. REPLY: Send a response to an email
4. ESCALATE: Forward to legal_team or chief_of_staff
5. DEFER: Mark for later processing

Guidelines:
- For high-value, high-urgency emails → reply immediately
- For legal/security risks → escalate immediately
- For low-value spam → classify and skip
- For unknown senders → defer initially
- Match reply tone to sender role (client: professional, internal: concise, vendor: brief)

Output your decision as JSON with these fields:
- action_type: "classify" | "reply" | "defer" | "escalate" | "prioritize"
- email_id: (optional) the email ID to act on
- label: (optional) "spam" | "normal" | "urgent" (for classify)
- content: (optional) reply text (for reply)
- priority_order: (optional) list of email IDs in order (for prioritize)
- escalate_to: (optional) "legal_team" | "chief_of_staff" (for escalate)
"""


def _build_user_prompt(observation: Observation) -> str:
    """Build user prompt from observation, hiding grader fields."""
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
            f"  Body: {email.body[:200]}{'...' if len(email.body) > 200 else ''}{thread_context}\n"
            f"  Priority: {email.priority_hint} | Deadline: {email.deadline_minutes}min | Value: {email.business_value}\n"
            f"  Risk tag: {email.risk_tag}"
        )

    lines.append("\nChoose the next action. Output ONLY valid JSON.")
    return "\n".join(lines)


def _parse_llm_response(text: str) -> dict[str, Any] | None:
    """Parse LLM response, handling various formats."""
    # Try direct JSON first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to extract JSON from markdown code blocks
    try:
        # Look for JSON in ```json or ``` blocks
        import re
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            return json.loads(match.group(1))
        
        # Try to find any {...} block
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
    except (json.JSONDecodeError, AttributeError):
        pass

    return None


def _validate_action(action_dict: dict[str, Any]) -> Action | None:
    """Validate action against Action schema."""
    try:
        # Check required field
        if "action_type" not in action_dict:
            return None
        
        action_type = action_dict["action_type"]
        valid_types: list[str] = ["classify", "reply", "defer", "escalate", "prioritize"]
        if action_type not in valid_types:
            return None

        # Build and validate action based on type
        action = Action(
            action_type=action_type,
            email_id=action_dict.get("email_id"),
            label=action_dict.get("label"),
            content=action_dict.get("content"),
            priority_order=action_dict.get("priority_order", []),
            escalate_to=action_dict.get("escalate_to"),
        )

        # Additional validation per action type
        if action_type == "classify":
            if not action.email_id or not action.label:
                return None
            valid_labels: list[str] = ["spam", "normal", "urgent"]
            if action.label not in valid_labels:
                return None
        elif action_type == "reply":
            if not action.email_id or not action.content:
                return None
        elif action_type == "defer":
            if not action.email_id:
                return None
        elif action_type == "escalate":
            if not action.email_id or not action.escalate_to:
                return None
            valid_targets = ["legal_team", "chief_of_staff"]
            if action.escalate_to not in valid_targets:
                return None
        elif action_type == "prioritize":
            if not action.priority_order:
                return None

        return action

    except Exception:
        return None


def _apply_guardrails(observation: Observation, action: Action, is_first: bool) -> Action:
    """Apply guardrails: first action prioritize, auto-escalate legal/security."""
    # Guardrail 1: First action should be prioritize (improves Kendall tau)
    if is_first and action.action_type != "prioritize":
        # Generate priority order based on sensible heuristics
        ranked = sorted(
            observation.emails,
            key=lambda e: (
                e.priority_hint == "high",
                e.business_value,
                -e.deadline_minutes,
            ),
            reverse=True,
        )
        return Action(
            action_type="prioritize",
            priority_order=[email.id for email in ranked],
        )

    # Guardrail 2: Auto-escalate legal/security risk emails
    if action.action_type in {"reply", "defer"}:
        target_email = None
        if action.email_id:
            for email in observation.emails:
                if email.id == action.email_id:
                    target_email = email
                    break
        
        if target_email and target_email.risk_tag in {"legal", "security"}:
            target = "legal_team" if target_email.risk_tag == "legal" else "chief_of_staff"
            return Action(
                action_type="escalate",
                email_id=action.email_id,
                escalate_to=target,
            )

    return action


class LLMAgent:
    """LLM-powered agent for email management decisions."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        temperature: float = DEFAULT_TEMPERATURE,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        self._model = model
        self._temperature = temperature
        self._timeout_seconds = timeout_seconds
        self._client: OpenAI | None = None
        self._did_prioritize = False

    def _get_client(self) -> OpenAI:
        """Lazy initialization of OpenAI client."""
        if self._client is None:
            # Get API configuration from environment variables
            api_base_url = os.environ.get("API_BASE_URL", "https://api.openai.com/v1")
            api_key = os.environ.get("HF_TOKEN", os.environ.get("OPENAI_API_KEY"))
            
            if not api_key:
                raise ValueError("HF_TOKEN or OPENAI_API_KEY environment variable not set")
            
            # Update model from env if available
            model = os.environ.get("MODEL_NAME", self._model)
            
            self._client = OpenAI(
                base_url=api_base_url,
                api_key=api_key,
                timeout=self._timeout_seconds,
            )
            # Update model if it came from env
            if model != self._model:
                self._model = model
        return self._client

    def get_action(self, observation: Observation) -> AIResponse:
        """
        Get action from LLM based on current observation.
        
        Applies guardrails:
        - First action always prioritizes (improves Kendall tau scoring)
        - Auto-escalates legal/security risk emails immediately
        
        Returns fallback on any failure:
        - timeout → fallback_timeout
        - parse error → fallback_parse_error  
        - validation error → fallback_validation_error
        - provider error → provider_error
        """
        is_first_action = not self._did_prioritize
        start_time = time.time()

        # Guardrail: Check if first action (return prioritize)
        if is_first_action:
            ranked = sorted(
                observation.emails,
                key=lambda e: (
                    e.priority_hint == "high",
                    e.business_value,
                    -e.deadline_minutes,
                ),
                reverse=True,
            )
            self._did_prioritize = True
            
            return AIResponse(
                action=Action(
                    action_type="prioritize",
                    priority_order=[email.id for email in ranked],
                ),
                trace=AIDecisionTrace(
                    reason="First action: prioritize emails by priority_hint, business_value, and deadline",
                    confidence=1.0,
                    alternatives_considered=[],
                    why_not="",
                    latency_ms=int((time.time() - start_time) * 1000),
                    model_name=self._model,
                    status="success",
                ),
            )

        # Guardrail: Auto-escalate legal/security risk emails
        for email in observation.emails:
            if email.risk_tag in {"legal", "security"}:
                target = "legal_team" if email.risk_tag == "legal" else "chief_of_staff"
                return AIResponse(
                    action=Action(
                        action_type="escalate",
                        email_id=email.id,
                        escalate_to=target,
                    ),
                    trace=AIDecisionTrace(
                        reason=f"Auto-escalate: {email.risk_tag} risk detected on email from {email.sender}",
                        confidence=1.0,
                        alternatives_considered=["reply", "defer", "classify"],
                        why_not="Legal/security risks must be escalated per policy",
                        latency_ms=int((time.time() - start_time) * 1000),
                        model_name=self._model,
                        status="success",
                    ),
                )

        # Call LLM
        try:
            client = self._get_client()
            response = client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": _build_user_prompt(observation)},
                ],
                temperature=self._temperature,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content
            if not content:
                return self._fallback_response("fallback_parse_error", start_time)

            action_dict = _parse_llm_response(content)
            if action_dict is None:
                return self._fallback_response("fallback_parse_error", start_time)

            action = _validate_action(action_dict)
            if action is None:
                return self._fallback_response("fallback_validation_error", start_time)

            # Apply remaining guardrails to LLM response
            action = _apply_guardrails(observation, action, is_first_action)

            # Track state
            if action.action_type == "prioritize":
                self._did_prioritize = True

            latency_ms = int((time.time() - start_time) * 1000)
            return AIResponse(
                action=action,
                trace=AIDecisionTrace(
                    reason=action_dict.get("reason", "LLM decision"),
                    confidence=action_dict.get("confidence", 0.5),
                    alternatives_considered=action_dict.get("alternatives_considered", []),
                    why_not=action_dict.get("why_not", ""),
                    latency_ms=latency_ms,
                    model_name=self._model,
                    status="success",
                ),
            )

        except Exception as e:
            error_str = str(e).lower()
            if "timeout" in error_str or "timed out" in error_str:
                return self._fallback_response("fallback_timeout", start_time)
            return self._fallback_response("provider_error", start_time)

    def _fallback_response(self, status: str, start_time: float) -> AIResponse:
        """Create fallback response on error."""
        latency_ms = int((time.time() - start_time) * 1000)
        
        # Return a safe fallback action (defer to first pending email)
        fallback_action = Action(
            action_type="defer",
            email_id=None,
        )
        
        status_map: dict[str, AIStatusType] = {
            "fallback_timeout": "fallback_timeout",
            "fallback_parse_error": "fallback_parse_error",
            "fallback_validation_error": "fallback_validation_error",
            "provider_error": "provider_error",
        }
        status_literal: AIStatusType = status_map.get(status, "provider_error")
        
        return AIResponse(
            action=fallback_action,
            trace=AIDecisionTrace(
                reason=f"LLM call failed: {status}",
                confidence=0.0,
                alternatives_considered=[],
                why_not="LLM unavailable, using fallback",
                latency_ms=latency_ms,
                model_name=self._model,
                status=status_literal,
            ),
        )

    def reset(self) -> None:
        """Reset agent state for new episode."""
        self._did_prioritize = False


# Default agent instance
_default_agent: LLMAgent | None = None


def get_action(observation: Observation) -> AIResponse:
    """Get action from default LLM agent."""
    global _default_agent
    if _default_agent is None:
        _default_agent = LLMAgent()
    return _default_agent.get_action(observation)


def reset_agent() -> None:
    """Reset default agent for new episode."""
    global _default_agent
    if _default_agent is not None:
        _default_agent.reset()
    _default_agent = None