"""Classifier agent for email classification."""

from env.models import Action, LabelType, Observation

from .base import BaseAgent

SPAM_KEYWORDS = ["spam", "unsubscribe", "promo", "discount", "click here"]
URGENT_KEYWORDS = ["urgent", "asap", "immediate", "critical", "deadline"]


class ClassifierAgent(BaseAgent):
    """Agent specialized in classifying emails into spam, normal, or urgent categories.

    The classifier analyzes email content to determine appropriate labels.
    """

    def __init__(self):
        super().__init__("ClassifierAgent")

    @property
    def system_prompt(self) -> str:
        return (
            "I am a ClassifierAgent responsible for labeling incoming emails. "
            "I analyze email sender, subject, and body to classify as 'spam', 'normal', or 'urgent'. "
            "I handle observations where emails have not yet been classified."
        )

    def can_handle(self, observation: Observation) -> bool:
        for email in observation.emails:
            if email.priority_hint == "high" or self._contains_keywords(
                email.subject, URGENT_KEYWORDS
            ):
                return True
            if self._contains_keywords(email.body, SPAM_KEYWORDS):
                return True
            if email.sender_role == "unknown":
                return True
        return False

    def execute(self, observation: Observation) -> Action | None:
        for email in observation.emails:
            label = self._determine_label(
                email.subject, email.body, email.sender_role, email.priority_hint
            )
            if label:
                return Action(action_type="classify", email_id=email.id, label=label)
        return None

    def _contains_keywords(self, text: str, keywords: list[str]) -> bool:
        text_lower = text.lower()
        return any(kw in text_lower for kw in keywords)

    def _determine_label(
        self, subject: str, body: str, sender_role: str, priority_hint: str
    ) -> LabelType | None:
        if self._contains_keywords(body, SPAM_KEYWORDS) or self._contains_keywords(
            subject, SPAM_KEYWORDS
        ):
            return "spam"
        if priority_hint == "high" or self._contains_keywords(subject, URGENT_KEYWORDS):
            return "urgent"
        if sender_role == "unknown":
            return "normal"
        return "normal"
