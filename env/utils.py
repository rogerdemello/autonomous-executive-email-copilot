from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from scipy.stats import kendalltau

from .data_loader import load_yaml
from .models import EmailRecord

DATA_ROOT = Path(__file__).resolve().parents[1] / "data"
SETTINGS_FILE = DATA_ROOT / "settings.yaml"


@dataclass(frozen=True)
class PersonaProfile:
    deadline_penalty_multiplier: float
    terminal_penalty_multiplier: float
    urgent_defer_penalty_multiplier: float
    redundant_penalty_multiplier: float


def _load_settings() -> dict:
    return load_yaml(SETTINGS_FILE)


def get_action_cost_minutes() -> dict[str, int]:
    data = _load_settings().get("action_cost_minutes", {})
    return {key: int(value) for key, value in data.items()}


def _load_persona_profiles() -> dict[str, PersonaProfile]:
    profile_data = _load_settings().get("persona_profiles", {})
    profiles: dict[str, PersonaProfile] = {}
    for persona, values in profile_data.items():
        profiles[persona] = PersonaProfile(
            deadline_penalty_multiplier=float(values["deadline_penalty_multiplier"]),
            terminal_penalty_multiplier=float(values["terminal_penalty_multiplier"]),
            urgent_defer_penalty_multiplier=float(values["urgent_defer_penalty_multiplier"]),
            redundant_penalty_multiplier=float(values["redundant_penalty_multiplier"]),
        )
    return profiles


def get_classifier_terms() -> tuple[list[str], list[str]]:
    terms = _load_settings().get("classifier_terms", {})
    spam_terms = [str(value).lower() for value in terms.get("spam", [])]
    urgent_terms = [str(value).lower() for value in terms.get("urgent", [])]
    return spam_terms, urgent_terms


def clip_score(value: float, low: float = 0.0, high: float = 1.0) -> float:
    if not math.isfinite(value):
        return low
    return max(low, min(high, value))


def strict_unit_interval(value: float, epsilon: float = 1e-6) -> float:
    bounded = clip_score(value)
    return min(1.0 - epsilon, max(epsilon, bounded))


def get_persona_profile(persona: str) -> PersonaProfile:
    profiles = _load_persona_profiles()
    if persona not in profiles:
        return profiles["balanced"]
    return profiles[persona]


def compute_gold_priority_order(emails: list[EmailRecord]) -> list[str]:
    def importance_weight(role: str) -> float:
        weights = {
            "client": 1.0,
            "internal": 0.7,
            "vendor": 0.5,
            "unknown": 0.3,
        }
        return weights.get(role, 0.3)

    def urgency_weight(priority_hint: str, deadline: int) -> float:
        hint_weight = {"high": 1.0, "medium": 0.6, "low": 0.2}[priority_hint]
        deadline_weight = max(0.0, 1.0 - (deadline / 240.0))
        return 0.65 * hint_weight + 0.35 * deadline_weight

    def rank_value(email: EmailRecord) -> float:
        urgency = urgency_weight(email.priority_hint, email.deadline_minutes)
        importance = importance_weight(email.sender_role)
        return (0.45 * urgency) + (0.30 * email.business_value) + (0.25 * importance)

    ranked = sorted(emails, key=rank_value, reverse=True)
    return [email.id for email in ranked]


def compute_pending_actions(emails: Iterable[EmailRecord]) -> list[str]:
    pending: list[str] = []
    for email in emails:
        if email.expected_label == "spam":
            continue
        if not email.resolved:
            pending.append(email.id)
    return pending


def derive_risk_level(emails: Iterable[EmailRecord]) -> str:
    critical_unresolved = [e for e in emails if e.critical and not e.resolved]
    if len(critical_unresolved) >= 2:
        return "high"
    if len(critical_unresolved) == 1:
        return "medium"
    return "low"


def reply_keyword_score(reply_text: str | None, expected_keywords: list[str]) -> float:
    if not expected_keywords:
        return 1.0
    if not reply_text:
        return 0.0

    text = reply_text.lower()
    hits = sum(1 for kw in expected_keywords if kw.lower() in text)
    return clip_score(hits / len(expected_keywords))


def ranking_similarity(predicted_order: list[str], gold_order: list[str]) -> float:
    if not predicted_order:
        return 0.0

    # Fill missing ids at the back to allow partial prioritization attempts.
    seen = set(predicted_order)
    completed_order = predicted_order + [eid for eid in gold_order if eid not in seen]

    index_gold = {eid: idx for idx, eid in enumerate(gold_order)}
    seq_pred = [index_gold[eid] for eid in completed_order if eid in index_gold]
    seq_gold = [index_gold[eid] for eid in gold_order]

    if len(seq_pred) < 2:
        return 0.0

    tau, _ = kendalltau(seq_pred, seq_gold)
    if tau is None:
        return 0.0

    # Map kendall tau from [-1, 1] into [0, 1].
    return clip_score((tau + 1.0) / 2.0)


def classify_heuristic(subject: str, body: str, priority_hint: str, risk_tag: str) -> str:
    text = (subject + " " + body).lower()
    spam_terms, urgent_terms = get_classifier_terms()

    if any(term in text for term in spam_terms):
        return "spam"

    if priority_hint == "high" or risk_tag in {"legal", "security"}:
        return "urgent"

    if any(term in text for term in urgent_terms):
        return "urgent"

    return "normal"
