"""In-memory mail provider — the zero-config default for local dev and tests.

No network, no credentials. Reads return a fixed set of messages; writes mutate
an in-memory mailbox dict so tests can assert exactly what the copilot did
(sent replies, drafts, labels, archives). This is what makes the whole pipeline
runnable and testable with no external accounts.
"""

from __future__ import annotations

from .base import FetchedMessage, MailProvider, WriteResult


def default_fixture_messages() -> list[FetchedMessage]:
    """A small, deterministic inbox spanning the copilot's decision paths.

    The wording deliberately hits the classifier's real spam/urgent vocabulary
    (see ``env.utils.get_classifier_terms``) so enrichment + classification
    produce a legal-escalation, an urgent-reply, a spam, and a normal-defer.
    """
    return [
        FetchedMessage(
            provider_message_id="m-legal-1",
            thread_id="t-legal-1",
            sender="counsel@lawfirm.example",
            sender_name="Outside Counsel",
            subject="URGENT: contract indemnification clause needs sign-off",
            body="Please review the indemnification and liability language in the NDA immediately.",
            references=[],
            received_at="2026-07-21T08:00:00+00:00",
        ),
        FetchedMessage(
            provider_message_id="m-urgent-1",
            thread_id="t-urgent-1",
            sender="priya@northwind.example",
            sender_name="Priya Nair",
            subject="ASAP: production outage on the billing service",
            body="The billing service failed for enterprise tenants. Need a status update urgently.",
            references=[],
            received_at="2026-07-21T08:05:00+00:00",
        ),
        FetchedMessage(
            provider_message_id="m-spam-1",
            thread_id="t-spam-1",
            sender="deals@promo.example",
            sender_name="Promo",
            subject="Limited deal: subscribe now for a discount",
            body="Register now for an exclusive offer and discount on your next order.",
            references=[],
            received_at="2026-07-21T08:10:00+00:00",
        ),
        FetchedMessage(
            provider_message_id="m-normal-1",
            thread_id="t-normal-1",
            sender="events@vendor.example",
            sender_name="Events Team",
            subject="Room booking for the quarterly offsite",
            body="Confirming the room reservation for the offsite next month. No rush.",
            references=[],
            received_at="2026-07-21T08:15:00+00:00",
        ),
    ]


class FakeProvider(MailProvider):
    """A mailbox held entirely in memory. Writes are recorded for assertions."""

    def __init__(self, messages: list[FetchedMessage] | None = None) -> None:
        self._messages = messages if messages is not None else default_fixture_messages()
        # Recorded side effects, keyed by kind.
        self.sent: list[dict] = []
        self.drafts: list[dict] = []
        self.labels: list[dict] = []
        self.archived: list[str] = []

    def fetch_messages(self, folder: str = "INBOX", limit: int = 25) -> list[FetchedMessage]:
        return list(self._messages[:limit])

    def send_reply(self, provider_message_id: str, body: str) -> WriteResult:
        self.sent.append({"message_id": provider_message_id, "body": body})
        return WriteResult(ok=True, provider_ref=f"sent-{provider_message_id}")

    def create_draft(self, provider_message_id: str, body: str) -> WriteResult:
        self.drafts.append({"message_id": provider_message_id, "body": body})
        return WriteResult(ok=True, provider_ref=f"draft-{provider_message_id}")

    def add_label(self, provider_message_id: str, label: str) -> WriteResult:
        self.labels.append({"message_id": provider_message_id, "label": label})
        return WriteResult(ok=True, provider_ref=f"label-{provider_message_id}-{label}")

    def archive(self, provider_message_id: str) -> WriteResult:
        self.archived.append(provider_message_id)
        return WriteResult(ok=True, provider_ref=f"archived-{provider_message_id}")
