"""LLM Agent for AI Chief of Staff - integrates OpenAI API with strict validation and fallback."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from openai import OpenAI

from .approval import get_approval_store
from .config import chat_client_kwargs, get_settings
from .models import (
    Action,
    AIDecisionTrace,
    AIResponse,
    AIStatusType,
    Observation,
    TokenUsage,
)
from .safety.guardrails import (
    FORBIDDEN_ESCALATION_TARGETS,
    PROMPT_INJECTION_PATTERNS,
    RISKY_REPLY_PATTERNS,
)
from .safety.guardrails import detect_prompt_injection as _detect_prompt_injection
from .safety.guardrails import detect_risky_content as _detect_risky_content
from .safety.guardrails import is_forbidden_escalation as _is_forbidden_escalation

__all__ = [
    "FORBIDDEN_ESCALATION_TARGETS",
    "PROMPT_INJECTION_PATTERNS",
    "RISKY_REPLY_PATTERNS",
]

logger = logging.getLogger(__name__)

# Cache configuration (must be before functions using them)
DEFAULT_CACHE_TTL_SECONDS = 3600
DEFAULT_CACHE_MAX_ENTRIES = 256
DEFAULT_CONFIDENCE_THRESHOLD = 0.7


def _compute_observation_hash(observation: Observation) -> str:
    obs_dict = observation.model_dump()
    obs_json = json.dumps(obs_dict, sort_keys=True)
    return hashlib.sha256(obs_json.encode()).hexdigest()[:32]


def _get_cached_response(
    observation: Observation, ttl: int = DEFAULT_CACHE_TTL_SECONDS
) -> AIResponse | None:
    obs_hash = _compute_observation_hash(observation)
    if obs_hash in _response_cache:
        cached_resp, timestamp = _response_cache[obs_hash]
        if time.time() - timestamp < ttl:
            cached_resp.cached = True
            logger.info(f"Cache hit for observation hash {obs_hash[:8]}...")
            return cached_resp
        else:
            del _response_cache[obs_hash]
    return None


def _cache_response(
    observation: Observation,
    response: AIResponse,
    max_entries: int = DEFAULT_CACHE_MAX_ENTRIES,
) -> None:
    obs_hash = _compute_observation_hash(observation)
    # Evict the oldest entries (insertion order) once the cache is full so it
    # cannot grow without bound now that it persists across calls.
    while len(_response_cache) >= max_entries and obs_hash not in _response_cache:
        oldest_key = next(iter(_response_cache))
        del _response_cache[oldest_key]
    _response_cache[obs_hash] = (response, time.time())


def _calculate_cost(model: str, usage: TokenUsage) -> float:
    pricing = MODEL_PRICING.get(model, {"prompt": 0.0, "completion": 0.0})
    prompt_cost = (usage.prompt_tokens / 1_000_000) * pricing["prompt"]
    completion_cost = (usage.completion_tokens / 1_000_000) * pricing["completion"]
    return prompt_cost + completion_cost


# Default configuration
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_LARGER_MODEL = "gpt-4o"
DEFAULT_TEMPERATURE = 0.2
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_AZURE_API_VERSION = "2024-02-15-preview"

# Model pricing (USD per 1M tokens)
MODEL_PRICING = {
    "gpt-4o-mini": {"prompt": 0.15, "completion": 0.60},
    "gpt-4o": {"prompt": 2.50, "completion": 10.00},
}

_response_cache: dict[str, tuple[AIResponse, float]] = {}


def _clear_cache() -> None:
    _response_cache.clear()


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
        require_approval: bool | None = None,
    ):
        self._model = model
        self._temperature = temperature
        self._timeout_seconds = timeout_seconds
        self._client: OpenAI | None = None
        self._did_prioritize = False
        # Emails already acted on this episode. The environment keeps listing every
        # email (handled or not), so without progress tracking the agent re-acts on
        # the same email forever and never works through the inbox.
        self._handled_ids: set[str] = set()
        # Human-in-the-loop approval gating for reply/escalate actions.
        # Off by default so the raw agent returns the action it decided on; the
        # API/product path can enable it (constructor arg or REQUIRE_APPROVAL env).
        if require_approval is None:
            require_approval = get_settings().require_approval
        self._require_approval = require_approval

    def _get_client(self) -> OpenAI:
        """Lazy initialization of OpenAI/Azure client."""
        if self._client is None:
            kwargs, model = chat_client_kwargs(self._timeout_seconds)
            self._client = OpenAI(**kwargs)
            # Prefer the configured model name over the constructor default.
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

        # Work the inbox one email at a time: hide already-handled emails so the
        # agent advances instead of re-deciding on the same email every step.
        pending_emails = [e for e in observation.emails if e.id not in self._handled_ids]
        if not pending_emails:
            return AIResponse(
                action=Action(action_type="defer", email_id=None),
                trace=AIDecisionTrace(
                    reason="All emails handled this episode",
                    confidence=1.0,
                    alternatives_considered=[],
                    why_not="",
                    latency_ms=int((time.time() - start_time) * 1000),
                    model_name=self._model,
                    status="success",
                ),
            )
        observation = observation.model_copy(update={"emails": pending_emails})

        # Guardrail: Auto-escalate legal/security risk emails (once each).
        for email in observation.emails:
            if email.risk_tag in {"legal", "security"}:
                target = "legal_team" if email.risk_tag == "legal" else "chief_of_staff"
                self._handled_ids.add(email.id)
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

        # Try cache first. Skip when approval is required so a cached response
        # can never short-circuit the human-approval gate below.
        if not self._require_approval:
            cached = _get_cached_response(observation)
            if cached:
                if cached.action and cached.action.email_id:
                    self._handled_ids.add(cached.action.email_id)
                logger.info("Cache hit - returning immediately (latency <1ms)")
                return cached

        # Dynamic model selection: small model first, larger fallback
        settings = get_settings()
        small_model = settings.model_name
        large_model = settings.larger_model
        confidence_threshold = settings.confidence_threshold
        current_model = small_model

        # Call LLM with dynamic model selection. No provider configured (or a bad
        # endpoint) surfaces here as a ValueError -> graceful provider_error fallback.
        try:
            client = self._get_client()
        except ValueError:
            return self._fallback_response("provider_error", start_time)
        retry_with_larger = False

        while True:
            try:
                response = client.chat.completions.create(
                    model=current_model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": _build_user_prompt(observation)},
                    ],
                    temperature=self._temperature,
                    response_format={"type": "json_object"},
                )

                content = response.choices[0].message.content
                break

            except Exception as e:
                error_str = str(e).lower()
                if "timeout" in error_str or "timed out" in error_str:
                    return self._fallback_response("fallback_timeout", start_time)
                # Retry once with larger model if small model fails
                if current_model == small_model and not retry_with_larger:
                    current_model = large_model
                    retry_with_larger = True
                    continue
                return self._fallback_response("provider_error", start_time)

        if not content:
            return self._fallback_response("fallback_parse_error", start_time)

        action_dict = _parse_llm_response(content)
        if action_dict is None:
            return self._fallback_response("fallback_parse_error", start_time)

        action = _validate_action(action_dict)
        if action is None:
            return self._fallback_response("fallback_validation_error", start_time)

        # Check confidence and fallback to larger model if needed
        confidence = action_dict.get("confidence", 0.5)
        if (
            current_model == small_model
            and confidence < confidence_threshold
            and not retry_with_larger
        ):
            retry_with_larger = True
            current_model = large_model
            try:
                response = client.chat.completions.create(
                    model=current_model,
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
                confidence = action_dict.get("confidence", 0.5)
            except Exception as exc:
                logger.warning("Larger-model confidence retry failed: %s", exc)

        # Apply remaining guardrails to LLM response
        if action:
            action = _apply_guardrails(observation, action, is_first_action)

        # Apply safety check
        if action:
            safe_action, safety_reason = self.safety_check(action, observation)
            if safe_action is None:
                return self._fallback_response(f"safety_{safety_reason}", start_time)
            action = safe_action

        if self._require_approval and action and action.action_type in {"escalate", "reply"}:
            if action.email_id:
                store = get_approval_store()
                pending = store.get_pending_requests()
                existing = [
                    p
                    for p in pending
                    if p.email_id == action.email_id and p.action_type == action.action_type
                ]
                if not existing:
                    approval_req = store.submit_request(
                        action_type=action.action_type,
                        email_id=action.email_id,
                        content=action.content,
                        escalate_to=action.escalate_to,
                    )
                    return AIResponse(
                        action=Action(action_type="defer", email_id=action.email_id),
                        trace=AIDecisionTrace(
                            reason=f"Pending approval for {action.action_type}: request {approval_req.id}",
                            confidence=1.0,
                            alternatives_considered=[],
                            why_not="Requires human approval",
                            latency_ms=int((time.time() - start_time) * 1000),
                            model_name=self._model,
                            status="success",
                        ),
                    )

        # Track state
        if action and action.action_type == "prioritize":
            self._did_prioritize = True

        # Track token usage and calculate cost
        usage = response.usage
        token_usage = TokenUsage(
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            total_tokens=usage.total_tokens if usage else 0,
        )
        cost_usd = _calculate_cost(current_model, token_usage)

        latency_ms = int((time.time() - start_time) * 1000)

        if action is None:
            return self._fallback_response("fallback_validation_error", start_time)

        ai_response = AIResponse(
            action=action,
            trace=AIDecisionTrace(
                reason=action_dict.get("reason", "LLM decision") if action_dict else "LLM decision",
                confidence=confidence,
                alternatives_considered=action_dict.get("alternatives_considered", [])
                if action_dict
                else [],
                why_not=action_dict.get("why_not", "") if action_dict else "",
                latency_ms=latency_ms,
                model_name=current_model,
                status="success",
                token_usage=token_usage,
                cost_usd=cost_usd,
            ),
        )

        logger.info(
            f"API call: model={current_model}, tokens={token_usage.total_tokens}, cost=${cost_usd:.4f}"
        )
        logger.info(
            f"Savings: small model tokens would have cost ~${_calculate_cost(small_model, token_usage):.4f}"
        )

        # Record LLM observability (telemetry must never break the agent).
        try:
            from telemetry.metrics import record_llm_usage

            record_llm_usage(
                latency_ms=latency_ms,
                cost_usd=cost_usd,
                prompt_tokens=token_usage.prompt_tokens,
                completion_tokens=token_usage.completion_tokens,
                model=current_model,
            )
        except Exception:  # noqa: BLE001 - telemetry is best-effort
            logger.debug("record_llm_usage failed", exc_info=True)

        if action.email_id:
            self._handled_ids.add(action.email_id)
        _cache_response(observation, ai_response)
        return ai_response

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
            "safety_prompt_injection_detected": "provider_error",
            "safety_forbidden_escalation_target": "provider_error",
            "safety_risky_reply_content": "provider_error",
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
        self._handled_ids.clear()

    def safety_check(
        self,
        action: Action,
        observation: Observation,
    ) -> tuple[Action | None, str | None]:
        """
        Analyze action for safety concerns.

        Checks:
        - Email content for prompt injection patterns
        - Escalation targets against forbidden list
        - Reply content for risky/unsafe patterns

        Returns:
        - (None, reason) if dangerous content detected (fallback)
        - (action, None) if safe
        """
        if not action.email_id:
            return action, None

        target_email = None
        for email in observation.emails:
            if email.id == action.email_id:
                target_email = email
                break

        if not target_email:
            return action, None

        if _detect_prompt_injection(target_email.body):
            return None, "prompt_injection_detected"

        if _detect_prompt_injection(target_email.subject):
            return None, "prompt_injection_detected"

        if action.action_type == "escalate" and _is_forbidden_escalation(action.escalate_to):
            return None, "forbidden_escalation_target"

        if action.action_type == "reply" and action.content:
            if _detect_risky_content(action.content):
                return None, "risky_reply_content"

        return action, None


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
