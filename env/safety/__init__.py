"""Safety primitives and the first-class safety metric.

``guardrails`` holds the pure detection functions (relocated verbatim from
``env.llm_agent`` so the live agent's behaviour is unchanged). ``metric`` turns a
graded trajectory into a bounded, out-of-band ``safety_score`` — reported
alongside the headline score, never folded into it.
"""

from .guardrails import (
    FORBIDDEN_ESCALATION_TARGETS,
    PROMPT_INJECTION_PATTERNS,
    RISKY_REPLY_PATTERNS,
    detect_prompt_injection,
    detect_risky_content,
    is_forbidden_escalation,
    scan_text,
)
from .metric import compute_safety_metric

__all__ = [
    "FORBIDDEN_ESCALATION_TARGETS",
    "PROMPT_INJECTION_PATTERNS",
    "RISKY_REPLY_PATTERNS",
    "detect_prompt_injection",
    "detect_risky_content",
    "is_forbidden_escalation",
    "scan_text",
    "compute_safety_metric",
]
