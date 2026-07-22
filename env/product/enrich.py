"""Turn a real message into an ``Observation`` with *inferred* signals.

The read-only connector's ``mapping.py`` fills real mail with constant neutral
defaults (priority=medium, deadline=240, risk=none, role=unknown), which makes
the policy's ranking inert — every message looks identical. This module infers
those signals from the message itself so prioritization and classification carry
real information, while still emitting ONLY the un-privileged ``Observation`` /
``ObservationEmail`` schema (no gold labels).

Inference reuses the same vocabulary the classifier uses
(:func:`env.utils.get_classifier_terms`) so enrichment and classification agree.
"""

from __future__ import annotations

from env.models import Observation, ObservationEmail, RiskTag, SenderRole, ThreadEntry
from env.utils import get_classifier_terms

from .providers.base import FetchedMessage

# Free email providers → treat the sender as an external "client" contact.
_FREEMAIL = {"gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com", "aol.com"}
# Automated/no-reply local parts → a vendor/system sender.
_VENDOR_HINTS = ("noreply", "no-reply", "notifications", "billing", "support", "donotreply")

# Risk vocabulary, checked in priority order (first hit wins).
_RISK_TERMS: list[tuple[RiskTag, tuple[str, ...]]] = [
    (
        "legal",
        ("contract", "legal", "lawsuit", "liability", "nda", "indemnif", "compliance", "gdpr"),
    ),
    (
        "security",
        ("breach", "security", "phishing", "malware", "credential", "vulnerab", "ransomware"),
    ),
    ("finance", ("invoice", "payment", "wire transfer", "refund", "billing", "forecast", "budget")),
    ("ops", ("outage", "downtime", "incident", "deploy", "sla")),
]

_DEADLINE_BY_PRIORITY = {"high": 60, "medium": 240, "low": 480}
_BUSINESS_VALUE_BY_ROLE = {"client": 0.9, "internal": 0.7, "vendor": 0.4, "unknown": 0.5}


def _domain(address: str) -> str:
    return address.rsplit("@", 1)[-1].lower() if "@" in address else ""


def infer_sender_role(sender: str, account_email: str) -> SenderRole:
    """Same-domain → internal; automated → vendor; freemail → client; else unknown."""
    sender_l = sender.lower()
    dom = _domain(sender_l)
    account_dom = _domain(account_email.lower())
    if dom and account_dom and dom == account_dom:
        return "internal"
    if any(hint in sender_l for hint in _VENDOR_HINTS):
        return "vendor"
    if dom in _FREEMAIL:
        return "client"
    return "unknown"


def infer_risk_tag(text: str) -> RiskTag:
    for tag, terms in _RISK_TERMS:
        if any(term in text for term in terms):
            return tag
    return "none"


def infer_priority(text: str, risk_tag: RiskTag, sender_role: SenderRole) -> str:
    _spam_terms, urgent_terms = get_classifier_terms()
    if risk_tag in {"legal", "security"} or any(t in text for t in urgent_terms):
        return "high"
    if sender_role == "vendor":
        return "low"
    return "medium"


def enrich_message(msg: FetchedMessage, *, account_email: str) -> ObservationEmail:
    """Map one fetched message to an ObservationEmail with inferred signals."""
    text = f"{msg.subject} {msg.body}".lower()
    sender_role = infer_sender_role(msg.sender, account_email)
    risk_tag = infer_risk_tag(text)
    priority = infer_priority(text, risk_tag, sender_role)
    business_value = _BUSINESS_VALUE_BY_ROLE[sender_role]
    if priority == "high":
        business_value = min(1.0, business_value + 0.1)
    thread_history = [ThreadEntry(from_address=ref, text="") for ref in msg.references]
    return ObservationEmail(
        id=msg.provider_message_id,
        sender=msg.sender,
        sender_role=sender_role,
        subject=msg.subject,
        body=msg.body,
        priority_hint=priority,  # type: ignore[arg-type]
        deadline_minutes=_DEADLINE_BY_PRIORITY[priority],
        business_value=business_value,
        risk_tag=risk_tag,
        thread_history=thread_history,
    )


def to_observation(
    messages: list[FetchedMessage], *, account_email: str, time_remaining: int = 240
) -> Observation:
    """Assemble an Observation from fetched messages with inferred signals."""
    emails = [enrich_message(m, account_email=account_email) for m in messages]
    high_risk = sum(1 for e in emails if e.risk_tag in {"legal", "security"})
    risk_level = "high" if high_risk >= 2 else "medium" if high_risk == 1 else "low"
    return Observation(
        emails=emails,
        time_remaining=time_remaining,
        pending_actions=[e.id for e in emails],
        risk_level=risk_level,  # type: ignore[arg-type]
        current_minute=0,
        persona="balanced",
        remaining_interruptions=0,
    )
