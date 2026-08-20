"""Model-written prose for a real mailbox message.

This is the product's LLM surface, and it is deliberately narrow: **the drafter
never decides anything**. By the time it is called, ``app.copilot.policy`` has
already ruled on whether a message gets a reply, an escalation, a label or
nothing, and that ruling is deterministic, reproducible and covered by tests.
The model only supplies words for a decision already made — which is why a model
outage, a bad key or a refusal degrades the prose and nothing else.

It is separate from :mod:`app.llm.agent` on purpose. That agent belongs to the
benchmark simulator: it *consumes* ``priority_hint`` / ``risk_tag`` as inputs and
its prompts talk about personas, ``time_remaining`` and remaining interruptions —
concepts a real mailbox does not have.

Every failure path returns ``None`` so the caller falls back to authored or
generic text. Nothing here may raise into a sync.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from app.copilot.providers.base import FetchedMessage
from app.core.config import get_settings
from app.core.models import ObservationEmail

from .parsing import extract_json_object
from .prompts.registry import registry
from .providers import LLMProvider, calculate_cost
from .safety.guardrails import detect_prompt_injection, detect_risky_content

logger = logging.getLogger(__name__)

# Long enough for three paragraphs of executive prose, short enough that a
# runaway generation cannot bankrupt a sync of sixty messages.
_MAX_TOKENS = 600
# Drafting is not a creative task — the same message should draft the same way.
_TEMPERATURE = 0.3
# Bodies are truncated before they reach the model: past this point a message is
# quoted history, and paying to read it makes the draft worse, not better.
_MAX_BODY_CHARS = 4000

_ACTION_BRIEFS = {
    "reply": (
        "Reply to this message. The reply will be held for the executive's "
        "approval before it is sent, so write it ready to send."
    ),
    "escalate": (
        "Do NOT answer the sender. Write a short handover note to the {target}, "
        "who will take this on: what has landed, why it is theirs, and what you "
        "need back. The executive approves this handover before it goes."
    ),
}


@dataclass(frozen=True)
class DraftResult:
    """Model-written prose for one proposed action."""

    body: str
    rationale: list[str] = field(default_factory=list)
    confidence: float = 0.0
    model: str = ""
    cost_usd: float = 0.0
    source: str = "llm"


@dataclass(frozen=True)
class DraftContext:
    """Who the copilot is writing as."""

    executive_name: str = "the executive"
    executive_role: str = "Chief Operating Officer"
    organisation: str = "the company"


def _clamp_confidence(value: object) -> float:
    try:
        return max(0.0, min(1.0, float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _clean_rationale(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value[:4] if str(item).strip()]


# Few-shot examples are style guidance, not content: past a couple of drafts the
# extra tokens stop changing the voice and start diluting the actual message.
_MAX_EXAMPLES = 3
_MAX_EXAMPLE_CHARS = 800


def _build_user_prompt(
    message: FetchedMessage,
    signals: ObservationEmail | None,
    examples: list[dict] | None = None,
) -> str:
    lines = [
        "MESSAGE",
        f"From: {message.sender_name or message.sender} <{message.sender}>",
        f"Subject: {message.subject}",
    ]
    if signals is not None:
        lines.append(
            f"Inferred signals: priority={signals.priority_hint}, "
            f"risk={signals.risk_tag}, respond within {signals.deadline_minutes} minutes"
        )
    body = (message.body or "").strip()
    if len(body) > _MAX_BODY_CHARS:
        body = body[:_MAX_BODY_CHARS] + "\n[truncated]"
    lines += ["", body, "", "END MESSAGE"]
    if examples:
        # Drafts this workspace's reviewers actually approved (or corrected and
        # sent). They demonstrate voice and length — the reply must still be
        # grounded in the MESSAGE above, never in the examples' facts.
        lines += [
            "",
            (
                "APPROVED PAST DRAFTS FROM THIS WORKSPACE "
                "(match their voice and length; do not reuse their facts):"
            ),
        ]
        for i, example in enumerate(examples[:_MAX_EXAMPLES], start=1):
            sample = str(example.get("body", "")).strip()[:_MAX_EXAMPLE_CHARS]
            subject = str(example.get("subject", "")).strip()
            lines += [f"--- Example {i}" + (f" (re: {subject})" if subject else ""), sample]
        lines.append("--- END EXAMPLES")
    return "\n".join(lines)


class EmailDrafter:
    """Writes reply and escalation prose. Returns ``None`` rather than failing."""

    def __init__(self, provider: LLMProvider | None = None) -> None:
        self._provider = provider

    def available(self) -> bool:
        return bool(self._provider) or get_settings().provider_available

    def _get_provider(self) -> LLMProvider | None:
        if self._provider is not None:
            return self._provider
        try:
            from .providers import auto_detect_provider

            self._provider = auto_detect_provider()
        except Exception as exc:  # noqa: BLE001 - no provider is a normal state
            logger.info("Email drafter disabled: %s", exc)
            return None
        return self._provider

    def draft(
        self,
        *,
        message: FetchedMessage,
        action_type: str,
        signals: ObservationEmail | None = None,
        escalate_to: str | None = None,
        context: DraftContext | None = None,
        examples: list[dict] | None = None,
    ) -> DraftResult | None:
        brief_template = _ACTION_BRIEFS.get(action_type)
        if brief_template is None:
            return None
        if not self.available():
            return None

        # Untrusted input, checked before it reaches the model rather than after.
        # A message that tries to rewrite the instructions does not get drafted at
        # all — it falls back to authored prose and still reaches a human, which
        # is the safe outcome for a phishing attempt.
        if detect_prompt_injection(f"{message.subject}\n{message.body}"):
            logger.warning(
                "Skipping LLM draft for %s: prompt-injection pattern in the message",
                message.provider_message_id,
            )
            return None

        provider = self._get_provider()
        if provider is None:
            return None

        ctx = context or DraftContext()
        target = (escalate_to or "specialist").replace("_", " ")
        system = registry.get("executive_draft")
        if system is None:  # pragma: no cover - registered at import
            return None

        messages = [
            {
                "role": "system",
                "content": system.render(
                    executive_name=ctx.executive_name,
                    executive_role=ctx.executive_role,
                    organisation=ctx.organisation,
                    action_brief=brief_template.format(target=target),
                    sender_role=(signals.sender_role if signals else "unknown"),
                ),
            },
            {"role": "user", "content": _build_user_prompt(message, signals, examples)},
        ]

        try:
            from telemetry.otel import in_span
        except ImportError:  # pragma: no cover - telemetry is optional
            from contextlib import nullcontext

            def in_span(name, attributes=None, kind=None):
                return nullcontext()

        started = time.monotonic()
        try:
            with in_span(
                "llm.draft",
                {
                    "action_type": action_type,
                    "provider_message_id": message.provider_message_id,
                },
            ):
                response = provider.generate(
                    messages,
                    temperature=_TEMPERATURE,
                    max_tokens=_MAX_TOKENS,
                )
        except Exception as exc:  # noqa: BLE001 - degrade to authored prose
            logger.warning("LLM draft failed for %s: %s", message.provider_message_id, exc)
            return None

        parsed = extract_json_object(response.content or "")
        if not parsed:
            logger.warning("LLM draft for %s was not JSON", message.provider_message_id)
            return None

        body = str(parsed.get("body") or "").strip()
        if not body:
            return None

        # Outbound check. The draft is about to be stored and shown as something
        # the executive might send under their own name.
        if detect_risky_content(body):
            logger.warning(
                "Discarding LLM draft for %s: risky content in the generated reply",
                message.provider_message_id,
            )
            return None

        cost = 0.0
        model = response.model or ""
        if response.usage:
            cost = calculate_cost(model, response.usage)
            try:
                from telemetry.metrics import record_llm_usage

                record_llm_usage(
                    latency_ms=(time.monotonic() - started) * 1000,
                    cost_usd=cost,
                    prompt_tokens=response.usage.prompt_tokens,
                    completion_tokens=response.usage.completion_tokens,
                    model=model or None,
                )
            except Exception:  # noqa: BLE001 - telemetry must never break a sync
                logger.debug("Could not record LLM usage", exc_info=True)

        return DraftResult(
            body=body,
            rationale=_clean_rationale(parsed.get("rationale")),
            confidence=_clamp_confidence(parsed.get("confidence")),
            model=model,
            cost_usd=cost,
        )


_default_drafter: EmailDrafter | None = None


def get_drafter() -> EmailDrafter:
    """Process-wide drafter. Stateless apart from its cached provider handle."""
    global _default_drafter
    if _default_drafter is None:
        _default_drafter = EmailDrafter()
    return _default_drafter


def reset_drafter() -> None:
    """Drop the cached drafter (tests, and after a settings change)."""
    global _default_drafter
    _default_drafter = None
