"""Escalator agent for handling email escalation."""

from .base import BaseAgent
from env.models import Action, Observation, RiskTag


ESCALATION_TARGETS = {
    "legal": "legal_team",
    "security": "security_team",
    "finance": "finance_team",
    "ops": "ops_team",
}

HIGH_RISK_KEYWORDS = ["legal", "security", "breach", "confidential", "compliance", "contract", "lawsuit"]


class EscalatorAgent(BaseAgent):
    """Agent specialized in escalating emails to appropriate teams.

    The escalator identifies emails that require specialist team handling.
    """

    def __init__(self):
        super().__init__("EscalatorAgent")

    @property
    def system_prompt(self) -> str:
        return (
            "I am an EscalatorAgent responsible for escalating emails to specialist teams. "
            "I identify emails with high-risk tags (legal, security, finance, ops) that require "
            "expert handling. I handle observations where emails need escalation."
        )

    def can_handle(self, observation: Observation) -> bool:
        if observation.risk_level == "high":
            return True
        for email in observation.emails:
            if email.risk_tag != "none" or self._contains_risk_keywords(email.subject, email.body):
                return True
        return False

    def execute(self, observation: Observation) -> Action | None:
        for email in observation.emails:
            escalate_to = self._determine_escalation(email.risk_tag, email.subject, email.body)
            if escalate_to:
                return Action(action_type="escalate", email_id=email.id, escalate_to=escalate_to)
        return None

    def _contains_risk_keywords(self, subject: str, body: str) -> bool:
        text = (subject + " " + body).lower()
        return any(kw in text for kw in HIGH_RISK_KEYWORDS)

    def _determine_escalation(self, risk_tag: RiskTag, subject: str, body: str) -> str | None:
        if risk_tag != "none" and risk_tag in ESCALATION_TARGETS:
            return ESCALATION_TARGETS[risk_tag]
        text = (subject + " " + body).lower()
        if "legal" in text or "contract" in text or "lawsuit" in text:
            return "legal_team"
        if "security" in text or "breach" in text or "confidential" in text:
            return "security_team"
        if "finance" in text or "payment" in text or "invoice" in text:
            return "finance_team"
        if "ops" in text or "outage" in text or "downtime" in text:
            return "ops_team"
        return None