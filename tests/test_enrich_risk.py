"""Risk-tag inference: the vocabulary must match words, not substrings.

Regression cover for a routing bug found by building the demo mailbox. Risk
terms were matched with a plain ``in`` test, so the three-letter entries fired
inside ordinary words — "nda" inside **Monday** and **agenda**, "sla" inside
**translate**. Any message mentioning a Monday deadline was tagged ``legal``,
which sends it to the legal team and marks it high priority with a 60-minute
deadline. That is a wrong escalation on very common English.
"""

from __future__ import annotations

import pytest

from app.copilot.enrich import enrich_message, infer_risk_tag
from app.copilot.providers.base import FetchedMessage


@pytest.mark.parametrize(
    "text",
    [
        "board pack goes out monday",
        "the agenda is attached",
        "standard terms apply",
        "our mandate is clear",
        "please translate the deck",
        "the islands team is onboard",
    ],
)
def test_ordinary_words_are_not_risk_signals(text: str) -> None:
    assert infer_risk_tag(text) == "none"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("please countersign the nda", "legal"),
        ("the indemnification clause", "legal"),
        ("we indemnify the supplier", "legal"),  # stem covers the word family
        ("both contracts are signed", "legal"),
        ("a gdpr subject access request", "legal"),
        ("credential phishing campaign", "security"),
        ("a vulnerability was reported", "security"),
        ("invoices are overdue", "finance"),
        ("change of wire transfer details", "finance"),
        ("billing service outage", "finance"),  # finance is checked before ops
        ("the sla was missed", "ops"),
        ("deployment caused downtime", "ops"),
    ],
)
def test_real_risk_signals_are_still_detected(text: str, expected: str) -> None:
    assert infer_risk_tag(text) == expected


def test_matching_is_case_insensitive() -> None:
    assert infer_risk_tag("Please review the NDA") == "legal"


def test_a_monday_deadline_does_not_become_a_legal_escalation() -> None:
    """The end-to-end shape of the bug, at the level a user would notice."""
    message = FetchedMessage(
        provider_message_id="m1",
        thread_id="t1",
        sender="chief.of.staff@northwind.example",
        sender_name="Chief of Staff",
        subject="Board deck — your slides by Monday",
        body="Board pack goes out Monday. Please send your two slides before then.",
    )
    enriched = enrich_message(message, account_email="alex@northwind.example")

    assert enriched.risk_tag == "none"
    assert enriched.priority_hint != "high"
    # A legal tag would have compressed this to the 60-minute high-priority bucket.
    assert enriched.deadline_minutes > 60
