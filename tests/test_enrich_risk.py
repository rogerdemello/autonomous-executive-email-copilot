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
        ("the sla was missed", "ops"),
        ("deployment caused downtime", "ops"),
    ],
)
def test_real_risk_signals_are_still_detected(text: str, expected: str) -> None:
    assert infer_risk_tag(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # One term each way. ops wins the tie, because the message is *about* the
        # incident and only mentions the money.
        ("billing service outage", "ops"),
        ("outage during the invoice run", "ops"),
        # Weight of evidence beats the tie-break: three finance terms, one ops.
        ("the invoice, payment and refund all missed the deploy window", "finance"),
        # Unambiguous money is still finance — nothing operational is claimed here.
        ("supplier changed bank details on a $340k invoice", "finance"),
        # legal and security stay on top regardless of how much else matches.
        ("gdpr request raised during the billing outage", "legal"),
        ("phishing campaign hit the invoice inbox during the outage", "security"),
    ],
)
def test_mixed_signals_go_to_the_tag_with_the_most_evidence(text: str, expected: str) -> None:
    """Risk is scored, not first-hit.

    Under first-hit-wins ``ops`` was unreachable in practice: any finance term
    anywhere in a message claimed it before ops was ever consulted, so genuine
    incidents were filed as finance.
    """
    assert infer_risk_tag(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "the contractor finished on friday",
        "three contractors are still onboarded",
    ],
)
def test_a_contractor_is_a_person_not_a_contract(text: str) -> None:
    """``contract*`` as a stem also swallows **contractor**.

    Found by widening the demo mailbox: "a former contractor's credentials are
    still active" is a security incident, and the stem match routed it to the
    legal team instead.
    """
    assert infer_risk_tag(text) == "none"


def test_contractor_credentials_are_a_security_matter() -> None:
    assert infer_risk_tag("former contractor credentials still active") == "security"


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
