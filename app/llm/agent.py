"""LLM Agent for AI Chief of Staff - integrates OpenAI API with strict validation and fallback."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any

from app.core.approval import get_approval_store
from app.core.config import get_settings
from app.core.models import (
    Action,
    AIDecisionTrace,
    AIResponse,
    AIStatusType,
    Observation,
    TokenUsage,
)

from .providers import LLMProvider, LLMResponse, ProviderCapability, calculate_cost
from .providers.openai_provider import OpenAIProvider
from .safety.guardrails import (
    FORBIDDEN_ESCALATION_TARGETS,
    PROMPT_INJECTION_PATTERNS,
    RISKY_REPLY_PATTERNS,
)
from .safety.guardrails import detect_prompt_injection as _detect_prompt_injection
from .safety.guardrails import detect_risky_content as _detect_risky_content
from .safety.guardrails import is_forbidden_escalation as _is_forbidden_escalation
from .tools import TOOL_DEFINITIONS, extract_action_from_tool_calls

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


# Default configuration
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_LARGER_MODEL = "gpt-4o"
DEFAULT_TEMPERATURE = 0.2
DEFAULT_TIMEOUT_SECONDS = 30.0

_response_cache: dict[str, tuple[AIResponse, float]] = {}
_cache_lock = asyncio.Lock()


def _clear_cache() -> None:
    _response_cache.clear()


async def _aget_cached_response(
    observation: Observation, ttl: int = DEFAULT_CACHE_TTL_SECONDS
) -> AIResponse | None:
    async with _cache_lock:
        return _get_cached_response(observation, ttl)


async def _acache_response(
    observation: Observation,
    response: AIResponse,
    max_entries: int = DEFAULT_CACHE_MAX_ENTRIES,
) -> None:
    async with _cache_lock:
        _cache_response(observation, response, max_entries)


# System prompt for AI Chief of Staff
SYSTEM_PROMPT = """You are an AI Chief of Staff helping an executive manage their inbox efficiently.

Your role is to make optimal email management decisions based on:
- Email priority (high/medium/low)
- Business value (0-1 scale)
- Deadline urgency
- Risk level
- Persona preferences (strict_ceo/balanced/chill_manager)

Use the available tools to take the appropriate action. Guidelines:
- For high-value, high-urgency emails → reply immediately
- For legal/security risks → escalate immediately
- For low-value spam → classify and skip
- For unknown senders → defer initially
- Match reply tone to sender role (client: professional, internal: concise, vendor: brief)
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
        self._provider: LLMProvider | None = None
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

    def _get_provider(self) -> LLMProvider:
        """Lazily resolve the configured LLM provider.

        Uses the provider registry's auto-detection, which honors ``LLM_PROVIDER``
        / the available credential (OpenAI, Azure, Anthropic, Gemini, Ollama) and
        wraps the result in a circuit breaker. We keep that wrapped provider —
        every provider exposes the same ``generate``/``agenerate`` surface, so the
        agent is provider-agnostic. Only if auto-detection finds no configured
        provider do we fall back to a bare OpenAI client, so the failure surfaces
        downstream as a clear auth error rather than a config error here.
        """
        if self._provider is None:
            from .providers import auto_detect_provider

            try:
                self._provider = auto_detect_provider()
            except ValueError:
                self._provider = OpenAIProvider(
                    model=self._model,
                    temperature=self._temperature,
                    timeout_seconds=self._timeout_seconds,
                )
        return self._provider

    def _call_llm_with_fallback(
        self,
        provider: LLMProvider,
        observation: Observation,
        small_model: str,
        large_model: str,
        confidence_threshold: float,
        use_tools: bool,
        start_time: float,
    ) -> tuple[LLMResponse | None, Action | None, float, str, str, str]:
        """Call the LLM with tool or JSON path, with dynamic model fallback.

        Returns (llm_response, action, confidence, reason, model_used, error_status).
        error_status is one of "fallback_timeout", "provider_error", "" on success.
        """
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(observation)},
        ]
        current_model = small_model
        retry_with_larger = False
        llm_response = None
        reason = "LLM decision"

        while True:
            # Step 1: Call the LLM (network call, can timeout)
            try:
                gen_kwargs: dict[str, Any] = {
                    "messages": messages,
                    "model": current_model,
                    "temperature": self._temperature,
                }
                if use_tools:
                    gen_kwargs["tools"] = TOOL_DEFINITIONS
                    gen_kwargs["response_format"] = None
                else:
                    gen_kwargs["response_format"] = {"type": "json_object"}
                    gen_kwargs["tools"] = None

                llm_response = provider.generate(**gen_kwargs)

            except Exception as e:
                error_str = str(e).lower()
                if "timeout" in error_str or "timed out" in error_str:
                    return None, None, 0.0, "", current_model, "fallback_timeout"
                if current_model == small_model and not retry_with_larger:
                    retry_with_larger = True
                    current_model = large_model
                    continue
                return None, None, 0.0, "", current_model, "provider_error"

            # Step 2: Parse the response (local, no network)
            action = None
            confidence = 0.0
            error_status = ""
            if use_tools and llm_response.tool_calls:
                action, metadata = extract_action_from_tool_calls(llm_response.tool_calls)
                if action:
                    confidence = metadata.get("confidence", 0.9)
                    reason = metadata.get("reason", f"Tool call: {action.action_type}")
                else:
                    error_status = "fallback_validation_error"
            elif llm_response.content:
                action_dict = _parse_llm_response(llm_response.content)
                if action_dict is None:
                    error_status = "fallback_parse_error"
                else:
                    action = _validate_action(action_dict)
                    if action:
                        confidence = action_dict.get("confidence", 0.5)
                        reason = action_dict.get("reason", "LLM decision")
                    else:
                        error_status = "fallback_validation_error"
            else:
                error_status = "fallback_parse_error"

            if action is None:
                # Parse/validation failure — retry with larger model if available
                if current_model == small_model and not retry_with_larger:
                    retry_with_larger = True
                    current_model = large_model
                    continue
                return None, None, 0.0, "", current_model, error_status or "fallback_parse_error"

            # Check if confidence is too low and we should retry with larger model
            if (
                current_model == small_model
                and confidence < confidence_threshold
                and not retry_with_larger
            ):
                retry_with_larger = True
                current_model = large_model
                continue

            return llm_response, action, confidence, reason, current_model, ""

    async def _acall_llm_with_fallback(
        self,
        provider: LLMProvider,
        observation: Observation,
        small_model: str,
        large_model: str,
        confidence_threshold: float,
        use_tools: bool,
        start_time: float,
    ) -> tuple[LLMResponse | None, Action | None, float, str, str, str]:
        """Async version of _call_llm_with_fallback using await provider.agenerate()."""
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(observation)},
        ]
        current_model = small_model
        retry_with_larger = False
        llm_response = None
        reason = "LLM decision"

        while True:
            try:
                gen_kwargs: dict[str, Any] = {
                    "messages": messages,
                    "model": current_model,
                    "temperature": self._temperature,
                }
                if use_tools:
                    gen_kwargs["tools"] = TOOL_DEFINITIONS
                    gen_kwargs["response_format"] = None
                else:
                    gen_kwargs["response_format"] = {"type": "json_object"}
                    gen_kwargs["tools"] = None

                llm_response = await provider.agenerate(**gen_kwargs)

            except Exception as e:
                error_str = str(e).lower()
                if "timeout" in error_str or "timed out" in error_str:
                    return None, None, 0.0, "", current_model, "fallback_timeout"
                if current_model == small_model and not retry_with_larger:
                    retry_with_larger = True
                    current_model = large_model
                    continue
                return None, None, 0.0, "", current_model, "provider_error"

            action = None
            confidence = 0.0
            error_status = ""
            if use_tools and llm_response.tool_calls:
                action, metadata = extract_action_from_tool_calls(llm_response.tool_calls)
                if action:
                    confidence = metadata.get("confidence", 0.9)
                    reason = metadata.get("reason", f"Tool call: {action.action_type}")
                else:
                    error_status = "fallback_validation_error"
            elif llm_response.content:
                action_dict = _parse_llm_response(llm_response.content)
                if action_dict is None:
                    error_status = "fallback_parse_error"
                else:
                    action = _validate_action(action_dict)
                    if action:
                        confidence = action_dict.get("confidence", 0.5)
                        reason = action_dict.get("reason", "LLM decision")
                    else:
                        error_status = "fallback_validation_error"
            else:
                error_status = "fallback_parse_error"

            if action is None:
                if current_model == small_model and not retry_with_larger:
                    retry_with_larger = True
                    current_model = large_model
                    continue
                return None, None, 0.0, "", current_model, error_status or "fallback_parse_error"

            if (
                current_model == small_model
                and confidence < confidence_threshold
                and not retry_with_larger
            ):
                retry_with_larger = True
                current_model = large_model
                continue

            return llm_response, action, confidence, reason, current_model, ""

    def _pre_process(
        self, observation: Observation, start_time: float
    ) -> tuple[AIResponse | None, Observation | None, bool]:
        """Run guardrail pre-checks before the LLM call.

        Returns (immediate_response, modified_observation, is_first_action).
        If immediate_response is not None, return it directly.
        """
        is_first_action = not self._did_prioritize

        # Guardrail: First action always prioritizes
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
            return (
                AIResponse(
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
                ),
                None,
                is_first_action,
            )

        # Filter handled emails
        pending_emails = [e for e in observation.emails if e.id not in self._handled_ids]
        if not pending_emails:
            return (
                AIResponse(
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
                ),
                None,
                is_first_action,
            )
        modified_obs = observation.model_copy(update={"emails": pending_emails})

        # Guardrail: Auto-escalate legal/security risk emails
        for email in modified_obs.emails:
            if email.risk_tag in {"legal", "security"}:
                target = "legal_team" if email.risk_tag == "legal" else "chief_of_staff"
                self._handled_ids.add(email.id)
                return (
                    AIResponse(
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
                    ),
                    None,
                    is_first_action,
                )

        return None, modified_obs, is_first_action

    def _build_ai_response(
        self,
        action: Action,
        llm_response: LLMResponse | None,
        confidence: float,
        reason: str,
        model_used: str,
        small_model: str,
        start_time: float,
        observation: Observation,
        is_first_action: bool,
    ) -> AIResponse:
        """Build final AIResponse, run safety/approval checks, cache result."""
        # Apply remaining guardrails
        action = _apply_guardrails(observation, action, is_first_action)

        # Safety check
        safe_action, safety_reason = self.safety_check(action, observation)
        if safe_action is None:
            return self._fallback_response(f"safety_{safety_reason}", start_time)
        action = safe_action

        # Approval gating
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

        # Token tracking
        usage = llm_response.usage if llm_response else None
        token_usage = usage or TokenUsage()
        cost_usd = calculate_cost(model_used, token_usage)
        latency_ms = int((time.time() - start_time) * 1000)

        ai_response = AIResponse(
            action=action,
            trace=AIDecisionTrace(
                reason=reason,
                confidence=confidence,
                alternatives_considered=[],
                why_not="",
                latency_ms=latency_ms,
                model_name=model_used,
                status="success",
                token_usage=token_usage,
                cost_usd=cost_usd,
            ),
        )

        logger.info(
            f"API call: model={model_used}, tokens={token_usage.total_tokens}, cost=${cost_usd:.4f}"
        )
        logger.info(
            f"Savings: small model tokens would have cost ~${calculate_cost(small_model, token_usage):.4f}"
        )

        # Telemetry (best-effort)
        try:
            from telemetry.metrics import record_llm_usage

            record_llm_usage(
                latency_ms=latency_ms,
                cost_usd=cost_usd,
                prompt_tokens=token_usage.prompt_tokens,
                completion_tokens=token_usage.completion_tokens,
                model=model_used,
            )
        except Exception:
            logger.debug("record_llm_usage failed", exc_info=True)

        if action.email_id:
            self._handled_ids.add(action.email_id)

        return ai_response

    def get_action(self, observation: Observation) -> AIResponse:
        """Get action from LLM (sync entry point)."""
        start_time = time.time()
        immediate, modified_obs, is_first = self._pre_process(observation, start_time)
        if immediate is not None:
            return immediate

        obs = modified_obs

        # Try cache
        if not self._require_approval:
            cached = _get_cached_response(obs)
            if cached:
                if cached.action and cached.action.email_id:
                    self._handled_ids.add(cached.action.email_id)
                logger.info("Cache hit - returning immediately (latency <1ms)")
                return cached

        # Provider + LLM call
        try:
            provider = self._get_provider()
        except ValueError as exc:
            logger.warning("Provider not available: %s", exc)
            return self._fallback_response("provider_error", start_time)

        settings = get_settings()
        small_model = settings.model_name
        use_tools = provider.supports(ProviderCapability.TOOLS) or provider.supports(
            ProviderCapability.FUNCTION_CALLING
        )

        llm_response, action, confidence, reason, model_used, error_status = (
            self._call_llm_with_fallback(
                provider=provider,
                observation=obs,
                small_model=small_model,
                large_model=settings.larger_model,
                confidence_threshold=settings.confidence_threshold,
                use_tools=use_tools,
                start_time=start_time,
            )
        )
        if action is None:
            return self._fallback_response(error_status or "fallback_parse_error", start_time)

        ai_response = self._build_ai_response(
            action=action,
            llm_response=llm_response,
            confidence=confidence,
            reason=reason,
            model_used=model_used,
            small_model=small_model,
            start_time=start_time,
            observation=obs,
            is_first_action=is_first,
        )

        _cache_response(obs, ai_response)
        return ai_response

    async def aget_action(self, observation: Observation) -> AIResponse:
        """Get action from LLM (async entry point)."""
        start_time = time.time()
        immediate, modified_obs, is_first = self._pre_process(observation, start_time)
        if immediate is not None:
            return immediate

        obs = modified_obs

        # Try cache (async)
        if not self._require_approval:
            cached = await _aget_cached_response(obs)
            if cached:
                if cached.action and cached.action.email_id:
                    self._handled_ids.add(cached.action.email_id)
                logger.info("Cache hit (async) - returning immediately")
                return cached

        # Provider + LLM call
        try:
            provider = self._get_provider()
        except ValueError as exc:
            logger.warning("Provider not available: %s", exc)
            return self._fallback_response("provider_error", start_time)

        settings = get_settings()
        small_model = settings.model_name
        use_tools = provider.supports(ProviderCapability.TOOLS) or provider.supports(
            ProviderCapability.FUNCTION_CALLING
        )

        (
            llm_response,
            action,
            confidence,
            reason,
            model_used,
            error_status,
        ) = await self._acall_llm_with_fallback(
            provider=provider,
            observation=obs,
            small_model=small_model,
            large_model=settings.larger_model,
            confidence_threshold=settings.confidence_threshold,
            use_tools=use_tools,
            start_time=start_time,
        )
        if action is None:
            return self._fallback_response(error_status or "fallback_parse_error", start_time)

        ai_response = self._build_ai_response(
            action=action,
            llm_response=llm_response,
            confidence=confidence,
            reason=reason,
            model_used=model_used,
            small_model=small_model,
            start_time=start_time,
            observation=obs,
            is_first_action=is_first,
        )

        await _acache_response(obs, ai_response)
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
    """Get action from default LLM agent (sync)."""
    global _default_agent
    if _default_agent is None:
        _default_agent = LLMAgent()
    return _default_agent.get_action(observation)


async def aget_action(observation: Observation) -> AIResponse:
    """Get action from default LLM agent (async)."""
    global _default_agent
    if _default_agent is None:
        _default_agent = LLMAgent()
    return await _default_agent.aget_action(observation)


def reset_agent() -> None:
    """Reset default agent for new episode."""
    global _default_agent
    if _default_agent is not None:
        _default_agent.reset()
    _default_agent = None
