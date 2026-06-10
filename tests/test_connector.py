"""Tests for the read-only email connector (no network — injected fake client)."""

from __future__ import annotations

from pathlib import Path

from env.connectors import to_observation_email
from env.connectors.base import RawEmail, ReadOnlyEmailConnector
from env.connectors.imap_readonly import ImapReadOnlyConnector, parse_raw_email
from env.models import Observation, ObservationEmail

_FIXTURE = Path(__file__).parent / "fixtures" / "mail" / "sample.eml"

_GOLD_FIELDS = {
    "expected_label",
    "expected_action",
    "expected_reply_keywords",
    "recommended_escalation",
    "critical",
}


class _FakeImap:
    """Minimal fake of the read-only IMAP surface used by the connector."""

    def __init__(self, messages: list[bytes]):
        self._messages = messages
        self.selected: str | None = None
        self.readonly: bool | None = None

    def select(self, mailbox: str, readonly: bool = False):
        self.selected = mailbox
        self.readonly = readonly
        return ("OK", [str(len(self._messages)).encode()])

    def search(self, charset, *criteria):
        ids = b" ".join(str(i + 1).encode() for i in range(len(self._messages)))
        return ("OK", [ids])

    def fetch(self, message_set, message_parts):
        idx = int(message_set) - 1
        return ("OK", [(b"meta", self._messages[idx])])


def test_parse_raw_email_from_fixture() -> None:
    raw = parse_raw_email(_FIXTURE.read_bytes(), fallback_id="fallback")
    assert raw.id == "<msg-001@acme.com>"
    assert "client@acme.com" in raw.sender
    assert raw.subject == "Contract review needed"
    assert "review the attached contract" in raw.body
    # In-Reply-To is prepended to References.
    assert "<prev-000@acme.com>" in raw.references
    assert "<thread-root@acme.com>" in raw.references


def test_to_observation_email_uses_neutral_defaults_and_no_gold_fields() -> None:
    raw = RawEmail(id="m1", sender="a@b.com", subject="Hi", body="Body", references=["r1"])
    oe = to_observation_email(raw)
    assert isinstance(oe, ObservationEmail)
    assert oe.priority_hint == "medium"
    assert oe.deadline_minutes == 240
    assert oe.business_value == 0.5
    assert oe.risk_tag == "none"
    assert oe.sender_role == "unknown"
    assert len(oe.thread_history) == 1
    # Ungradeable by construction: no gold answer fields exist on the observation.
    assert _GOLD_FIELDS.isdisjoint(oe.model_dump().keys())


def test_connector_fetches_observation_readonly() -> None:
    fake = _FakeImap([_FIXTURE.read_bytes()])
    connector = ImapReadOnlyConnector(fake)
    obs = connector.fetch_observation(limit=10)
    assert isinstance(obs, Observation)
    assert len(obs.emails) == 1
    assert obs.emails[0].subject == "Contract review needed"
    # The load-bearing guarantee: the mailbox was selected read-only.
    assert fake.readonly is True


def test_connector_exposes_no_mutating_methods() -> None:
    surface = set(dir(ReadOnlyEmailConnector)) | set(dir(ImapReadOnlyConnector))
    for forbidden in ("send", "delete", "store", "copy", "move", "expunge", "append"):
        assert forbidden not in surface, f"connector must not expose '{forbidden}'"
