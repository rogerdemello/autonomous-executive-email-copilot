"""Responder agent for drafting email responses."""

from env.models import Action, Observation

from .base import BaseAgent

RESPONDABLE_KEYWORDS = ["question", "help", "need", "request", "please", "can you", "when", "how"]


class ResponderAgent(BaseAgent):
    """Agent specialized in drafting email responses.

    The responder creates appropriate replies to emails that require responses.
    """

    def __init__(self):
        super().__init__("ResponderAgent")

    @property
    def system_prompt(self) -> str:
        return (
            "I am a ResponderAgent responsible for drafting email responses. "
            "I analyze email content and sender to craft appropriate replies. "
            "I handle observations where emails require a response (non-spam, non-urgent)."
        )

    def can_handle(self, observation: Observation) -> bool:
        for email in observation.emails:
            if self._requires_response(email.subject, email.body, email.sender_role):
                return True
        return False

    def execute(self, observation: Observation) -> Action | None:
        for email in observation.emails:
            if self._requires_response(email.subject, email.body, email.sender_role):
                response = self._draft_response(email.subject, email.body, email.sender_role)
                return Action(action_type="reply", email_id=email.id, content=response)
        return None

    def _requires_response(self, subject: str, body: str, sender_role: str) -> bool:
        body_lower = body.lower()
        if sender_role == "unknown":
            return False
        return any(kw in body_lower for kw in RESPONDABLE_KEYWORDS)

    def _draft_response(self, subject: str, body: str, sender_role: str) -> str:
        subject_lower = subject.lower()
        if "meeting" in subject_lower:
            return "Thank you for your message about the meeting. I'll review and get back to you shortly."
        if "question" in subject_lower:
            return "Thanks for reaching out. Let me look into this and respond with more details."
        if "help" in subject_lower or "need" in subject_lower:
            return (
                "I received your request and am looking into how I can help. I'll follow up soon."
            )
        return "Thank you for your email. I'll review and respond appropriately."
