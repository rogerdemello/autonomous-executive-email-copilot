"""Turn a real message into an ``Observation`` with *inferred* signals.

The read-only connector's ``mapping.py`` fills real mail with constant neutral
defaults (priority=medium, deadline=240, risk=none, role=unknown), which makes
the policy's ranking inert — every message looks identical. This module infers
those signals from the message itself so prioritization and classification carry
real information, while still emitting ONLY the un-privileged ``Observation`` /
``ObservationEmail`` schema (no gold labels).

Inference reuses the same vocabulary the classifier uses
(:func:`app.core.utils.get_classifier_terms`) so enrichment and classification agree.
"""

from __future__ import annotations

import re

from app.core.models import Observation, ObservationEmail, RiskTag, SenderRole, ThreadEntry
from app.core.utils import get_classifier_terms

from .providers.base import FetchedMessage

# Free email providers → treat the sender as an external "client" contact.
_FREEMAIL = {"gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com", "aol.com"}
# Automated/no-reply local parts → a vendor/system sender.
_VENDOR_HINTS = ("noreply", "no-reply", "notifications", "billing", "support", "donotreply")

# Risk vocabulary. The tag with the most distinct term matches wins; ties break
# in the listed order, which is why that order is severity-descending.
#
# ``ops`` sits above ``finance`` deliberately. A message is usually *about* the
# incident and only *mentions* the money: "billing service outage" matches one
# term on each side, and calling that finance buries an operational incident
# under the wrong owner. Under the previous first-hit-wins rule ``ops`` was
# effectively unreachable — any finance word anywhere in the body claimed the
# message first. ``legal`` and ``security`` stay on top, so the escalation gate
# in :mod:`app.copilot.policy` sees exactly what it saw before.
#
# A trailing ``*`` means "this is a stem, match the whole word family"
# (``indemnif*`` covers indemnify/indemnified/indemnification). Everything else
# matches as a whole word only.
#
# Whole-word matching is not fussiness. These were once plain substring checks,
# and short terms quietly wrecked the routing: "nda" fires on **Monday** and
# **agenda**, "sla" on **translate** and **legislation**. Any message mentioning
# a Monday deadline was being escalated to the legal team.
_RISK_TERMS: list[tuple[RiskTag, tuple[str, ...]]] = [
    (
        # "contract" is listed as its own word family rather than a ``contract*``
        # stem, because that stem also swallows **contractor** and
        # **contractors** — people, not agreements. It sent "a former
        # contractor's credentials are still active" to the legal team instead of
        # to security, which is the same class of mistake as the old "nda inside
        # Monday", one level further in.
        "legal",
        (
            "contract",
            "contracts",
            "contractual",
            "legal*",
            "lawsuit*",
            "liability",
            "nda",
            "indemnif*",
            "compliance",
            "gdpr",
        ),
    ),
    (
        "security",
        ("breach*", "security", "phishing", "malware", "credential*", "vulnerab*", "ransomware"),
    ),
    ("ops", ("outage*", "downtime", "incident*", "deploy*", "sla")),
    (
        "finance",
        ("invoice*", "payment*", "wire transfer", "refund*", "billing", "forecast*", "budget*"),
    ),
]


def _compile(term: str) -> re.Pattern[str]:
    """One pattern per term: stems match their word family, others whole words."""
    body = rf"{re.escape(term[:-1])}\w*" if term.endswith("*") else rf"{re.escape(term)}\b"
    return re.compile(r"\b(?:" + body + r")", re.IGNORECASE)


# One pattern per *term* rather than one alternation per tag, so a tag's score is
# the number of distinct terms it matched — not how often any single word recurs.
_RISK_PATTERNS: list[tuple[RiskTag, tuple[re.Pattern[str], ...]]] = [
    (tag, tuple(_compile(term) for term in terms)) for tag, terms in _RISK_TERMS
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
    """The risk tag with the most distinct term matches; ties break by severity.

    Scoring rather than first-hit matters for mixed messages. "Billing service
    outage" carries one finance word and one ops word: whichever tag is merely
    checked first would otherwise claim it outright, regardless of how much
    evidence the other side had.
    """
    best: RiskTag = "none"
    best_score = 0
    for tag, patterns in _RISK_PATTERNS:
        score = sum(1 for pattern in patterns if pattern.search(text))
        # Strictly greater: on a tie the earlier (more severe) tag already won.
        if score > best_score:
            best, best_score = tag, score
    return best


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
